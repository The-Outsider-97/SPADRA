"""Round trips: a model or drawing made here, exported, and imported into a fresh project must come back with the same geometry, and the
conversion report must say what the format cannot keep. Formats whose library is not installed are skipped, never claimed: DXF needs ezdxf
(the CI jobs with interop ``on`` install it and fail, rather than skip, when it is missing). GLB's own round trip and the unit scaling of STL are in test_interop.py and are not repeated here.

IFC is import-only (SPADRA has no IFC writer), so it cannot make a round trip; an IFC import needs a reference file from an authoring tool."""
from __future__ import annotations

import base64
import importlib.util
import os
import unittest
from collections import Counter

from applications.SPADRA import interop as IO
from applications.SPADRA.project import Project

HAS_EZDXF = importlib.util.find_spec("ezdxf") is not None
HAS_OCP = importlib.util.find_spec("OCP") is not None
try:
    from jsonschema import Draft202012Validator  # noqa: F401  - SGEO and SDRW validate against 2020-12 schemas
    HAS_SCHEMA_VALIDATOR = True
except ImportError:
    HAS_SCHEMA_VALIDATOR = False

# In CI a missing library is a failure, not a skip, or a broken install would turn the whole suite green: SPADRA_REQUIRE_INTEROP=1 is set by the
# jobs that install requirements-interop.txt, and GitHub sets CI=true for every job (requirements.txt always installs jsonschema).
REQUIRE_INTEROP = os.environ.get("SPADRA_REQUIRE_INTEROP") == "1"
REQUIRE_SCHEMA = os.environ.get("CI") == "true"

FLOAT32 = 1e-5                                   # GLB, STL and OBJ store single precision


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def vertex_count(scene) -> int:
    return sum(len(o["vertices"]) for o in scene["objects"])


def entities_of(drawing) -> Counter:
    """What a drawing holds, as (kind, rounded geometry) pairs, so two drawings can be compared whatever order their entities are in."""
    def rounded(value):
        return round(value, 6) if isinstance(value, float) else [rounded(v) for v in value] if isinstance(value, list) else value
    return Counter((e["kind"], repr({k: rounded(v) for k, v in sorted(e["geometry"].items())})) for e in (drawing.get(i) for i in IO._page_ids(drawing)))


class Source(unittest.TestCase):
    def setUp(self) -> None:
        self.a = Project.create("Source")
        level = self.a.manifest["levels"][0]["id"]
        self.a.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})
        self.a.execute("model.add_floor", {"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]], "level": level, "thickness": 200})
        self.plan = next(v["id"] for v in self.a.manifest["views"] if v["kind"] == "plan")


class ModelRoundTrip(Source):
    # What each format keeps. A mesh format has no object identity, so every object comes back as one.
    KEEPS_OBJECTS = {"glb": True, "obj": True, "stl": False, "sgeo": True}
    RENAMED = {"obj": lambda name: name.replace(" ", "_")}                    # OBJ names cannot hold spaces; the export report says so

    def round_trip(self, fmt):
        raw, export_report = IO.export_model(self.a, fmt)
        b = Project.create("Target")
        out = b.execute("model.import", {"format": fmt, "data": b64(raw), "name": f"file.{fmt}", "unit": "m"})["result"]
        return b, out, export_report

    def check_geometry(self, fmt):
        b, out, export_report = self.round_trip(fmt)
        src, dst = self.a.scene_data(), b.scene_data()
        for lo, hi in zip(src["bounds"], dst["bounds"]):
            for x, y in zip(lo, hi):
                self.assertAlmostEqual(x, y, delta=FLOAT32, msg=f"{fmt}: the overall extent moved")
        self.assertEqual(vertex_count(dst), vertex_count(src), f"{fmt}: vertices were lost or added")
        if self.KEEPS_OBJECTS[fmt]:
            rename = self.RENAMED.get(fmt, lambda name: name)
            self.assertEqual(sorted(o["name"] for o in dst["objects"]), sorted(rename(o["name"]) for o in src["objects"]), f"{fmt}: object names changed")
        else:
            self.assertEqual(len(dst["objects"]), 1, f"{fmt}: a mesh has no objects, so the model should come back as one")
        self.assertIn("accuracy_class", export_report)
        return out, export_report

    def test_glb_keeps_geometry_and_object_names(self):
        self.check_geometry("glb")

    def test_obj_keeps_geometry_and_object_names(self):
        out, export_report = self.check_geometry("obj")
        self.assertTrue(any("unit" in w for w in out["report"]["warnings"]), "an OBJ file states no unit, and the report must say one was assumed")
        self.assertTrue(any("spaces" in a for a in export_report["assumptions"]), "renamed objects must be reported, not silently changed")

    def test_stl_keeps_geometry_but_merges_objects_and_says_the_unit_was_assumed(self):
        out, _ = self.check_geometry("stl")
        self.assertTrue(any("unit" in w for w in out["report"]["warnings"]), "an STL file states no unit, and the report must say one was assumed")

    @unittest.skipUnless(HAS_SCHEMA_VALIDATOR or REQUIRE_SCHEMA, "needs jsonschema >= 4 to validate an SGEO file")
    def test_native_sgeo_keeps_geometry_and_object_names(self):
        self.check_geometry("sgeo")


class DrawingRoundTrip(Source):
    def setUp(self) -> None:
        super().setUp()
        self.a.execute("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [1000, 0]})
        self.a.execute("draw.line", {"drawing": self.plan, "start": [200, 100], "end": [900, 750]})
        self.a.execute("draw.circle", {"drawing": self.plan, "center": [500, 500], "radius": 250})

    def round_trip(self, fmt):
        raw, _ = IO.export_drawing(self.a.drawing(self.plan), fmt, name="plan")
        b = Project.create("Target")
        plan = next(v["id"] for v in b.manifest["views"] if v["kind"] == "plan")
        b.execute("draw.import", {"drawing": plan, "format": fmt, "data": b64(raw), "unit": self.a.drawing(self.plan).unit})
        return entities_of(self.a.drawing(self.plan)), entities_of(b.drawing(plan))

    def test_svg_keeps_every_entity_and_its_geometry(self):
        before, after = self.round_trip("svg")
        self.assertEqual(sum(before.values()), 3)
        self.assertEqual(after, before)

    @unittest.skipUnless(HAS_SCHEMA_VALIDATOR or REQUIRE_SCHEMA, "needs jsonschema >= 4 to validate an SDRW file")
    def test_native_sdrw_keeps_every_entity_and_its_geometry(self):
        before, after = self.round_trip("sdrw")
        self.assertEqual(after, before)

    @unittest.skipUnless(HAS_EZDXF or REQUIRE_INTEROP, "ezdxf is not installed, so DXF conversion was not run here")
    def test_dxf_keeps_every_entity_and_its_geometry(self):
        before, after = self.round_trip("dxf")
        self.assertEqual(after, before)


class WhatIsNotConverted(unittest.TestCase):
    def test_conversions_that_do_not_exist_say_so_and_why(self):
        investigated = IO.availability()["investigated"]
        for key in ("ifcExport", "gltf", "pdf"):
            self.assertFalse(investigated[key]["available"], key)
            self.assertTrue(investigated[key]["reason"], key)
        self.assertEqual("OCP" in investigated["step"]["reason"], not HAS_OCP, "STEP names the missing Open CASCADE exactly when it is missing")
        self.assertFalse(investigated["step"]["available"], "STEP retains the file and writes one box; it is not a conversion")


if __name__ == "__main__":
    unittest.main()
