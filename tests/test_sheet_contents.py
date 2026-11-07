"""What a sheet can hold beyond plans, sections, elevations and schedules: the 3D model from a camera direction, a picture of the 3D view and a report."""
from __future__ import annotations

import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from applications.SPADRA import sheetviews as SV
from applications.SPADRA import sheets as SH
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, LimitExceeded

from .test_bim import Case


def png(width: int, height: int, colour=(200, 60, 40)) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    row = b"\x00" + bytes(colour) * width
    return SV.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


class Contents(Case):
    def setUp(self) -> None:
        super().setUp()
        self.first_wall = self.wall()
        self.view3d = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "3d")
        self.sheet = self.run_("sheet.create", name="Overview")["id"]

    def vps(self):
        return self.p.sheet_data(self.sheet)["viewports"]

    def test_the_model_is_placed_as_vector_faces_from_a_camera_direction(self):
        vp = self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d, azimuth=-45, elevation=30, scale="1:100")["id"]
        shown = self.vps()[0]
        self.assertEqual((shown["kind"], shown["id"], shown["azimuth"], shown["elevation"]), ("model", vp, -45.0, 30.0))
        faces = shown["solid"]["faces"]
        self.assertGreater(len(faces), 0)
        self.assertTrue(all(len(f) == 9 for f in faces))
        self.assertTrue(0.6 <= min(f[6] for f in faces) and max(f[6] for f in faces) <= 1.0)
        self.assertGreater(shown["solid"]["bounds"][1][0], shown["solid"]["bounds"][0][0])

    def test_shared_and_instance_material_colors_reach_vector_sheets_and_issued_digests(self):
        self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d)
        self.run_("sheet.issue", id=self.sheet, description="First issue")
        before = self.vps()[0]["solid"]["faces"]
        self.run_("model.set_property", id=self.first_wall, key="material_color", value="#224466")
        faces = self.vps()[0]["solid"]["faces"]
        self.assertEqual({f[8] for f in faces}, {0x224466})
        self.assertNotEqual(faces, before)
        self.assertEqual(self.p.sheet_data(self.sheet)["status"]["state"], "changed")
        self.run_("model.set_property", id=self.first_wall, key="color", value="#AABBCC")
        self.assertEqual({f[8] for f in self.vps()[0]["solid"]["faces"]}, {0xAABBCC})

    def test_individual_layer_colors_are_carried_by_model_sheet_faces(self):
        additional = self.run_("model.add_layer", id=self.first_wall, material="Timber", thickness=50)["layer"]
        self.run_("model.set_property", id=self.first_wall, key=f"layer:{additional}:color", value="#EEDDCC")
        data = SV.model_view(self.p, -45.0, 30.0, 1.0)
        self.assertIn(0xEEDDCC, {f[8] for f in data["faces"]})

    def test_native_sldc_preserves_the_model_sheets_material_colors_as_editable_data(self):
        self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d)
        self.run_("model.set_property", id=self.first_wall, key="color", value="#345678")
        raw, _ = SH.to_sldc(self.p, self.sheet)
        restored = SH.read_sldc(raw)
        faces = restored["sheet"]["viewports"][0]["solid"]["faces"]
        self.assertTrue(faces)
        self.assertEqual({f[8] for f in faces}, {0x345678})
        self.assertTrue(SH.verify_sldc(self.p, raw, self.sheet)["identical"])

    def test_a_box_seen_from_a_corner_shows_three_sides(self):
        # a single closed box: from a corner and above only the three faces that face the viewer are drawn, each as two triangles
        data = SV.model_view(self.p, -45.0, 30.0, 1.0)
        wall_faces = len(data["faces"])
        self.assertEqual(wall_faces, 6)                                # a wall is a box: top and two long sides plus one end = three quads = six triangles
        back = SV.model_view(self.p, 135.0, 30.0, 1.0)
        self.assertEqual(len(back["faces"]), 6)
        self.assertNotEqual(data["faces"], back["faces"])

    def test_the_view_follows_the_model_and_marks_an_issued_sheet_as_changed(self):
        self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d)
        before = self.vps()[0]["solid"]["bounds"]
        self.run_("sheet.issue", id=self.sheet, description="First issue")
        self.assertEqual(self.p.sheet_data(self.sheet)["status"]["state"], "current")
        self.wall(a=(0, 3000), b=(0, 6000))
        self.assertNotEqual(self.vps()[0]["solid"]["bounds"], before)
        status = self.p.sheet_data(self.sheet)["status"]
        self.assertEqual(status["state"], "changed")
        self.assertIn("3D Model", status["changed"][0])

    def test_the_camera_is_editable_and_checked(self):
        vp = self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d)["id"]
        self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="azimuth", value=100)
        self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="elevation", value=60)
        self.assertEqual((self.vps()[0]["azimuth"], self.vps()[0]["elevation"]), (100.0, 60.0))
        with self.assertRaises(BadRequest):
            self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="elevation", value=90)
        with self.assertRaises(BadRequest):
            self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="width", value=50)
        self.p.undo()                                                   # the refused edits left nothing to undo; this takes back the elevation
        self.assertEqual((self.vps()[0]["azimuth"], self.vps()[0]["elevation"]), (100.0, 30.0))

    def test_an_empty_model_cannot_be_placed(self):
        empty = Project.create("Empty")
        sheet = empty.execute("sheet.create", {"name": "S"})["result"]["id"]
        view3d = next(v["id"] for v in empty.manifest["views"] if v["kind"] == "3d")
        with self.assertRaises(BadRequest):
            empty.execute("sheet.place_view", {"id": sheet, "view_id": view3d})

    def test_a_snapshot_is_stored_sized_and_does_not_follow_the_model(self):
        data = png(40, 20)
        out = self.run_("sheet.place_snapshot", id=self.sheet, data=base64.b64encode(data).decode(), camera={"azimuth": -50, "elevation": 30}, width=100, title="From the south-east")
        shown = self.vps()[0]
        self.assertEqual((shown["kind"], shown["paper_width"], shown["paper_height"]), ("snapshot", 100.0, 50.0))
        self.assertTrue(shown["image"]["href"].startswith("data:image/png;base64,"))
        self.assertEqual((shown["image"]["width_px"], shown["image"]["height_px"], shown["image"]["camera"]["azimuth"]), (40, 20, -50))
        self.run_("sheet.issue", id=self.sheet, description="First issue")
        self.wall(a=(0, 3000), b=(0, 6000))
        self.assertEqual(self.p.sheet_data(self.sheet)["status"]["state"], "current")          # a picture does not change when the model does
        self.run_("sheet.set_viewport", id=self.sheet, viewport=out["id"], key="width", value=150)
        self.assertEqual(self.vps()[0]["paper_height"], 75.0)
        self.assertEqual(self.p.sheet_data(self.sheet)["status"]["state"], "changed")

    def test_a_snapshot_must_be_a_png_of_sane_size(self):
        for bad in ("", "not base64!", base64.b64encode(b"GIF89a").decode(), base64.b64encode(png(40, 20)[:20]).decode()):
            with self.assertRaises(BadRequest):
                self.run_("sheet.place_snapshot", id=self.sheet, data=bad)
        huge = bytearray(png(10, 10))
        huge[16:20] = struct.pack(">I", 20000)
        with self.assertRaises(BadRequest):
            self.run_("sheet.place_snapshot", id=self.sheet, data=base64.b64encode(bytes(huge)).decode())
        with self.assertRaises(LimitExceeded):
            SV.png_size(SV.PNG_MAGIC + b"\0" * (SV.PNG_LIMIT + 1))
        self.assertEqual(self.vps(), [])

    def test_snapshots_are_saved_with_the_project_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj.spadra"
            self.p.root = root
            vp = self.run_("sheet.place_snapshot", id=self.sheet, data=base64.b64encode(png(8, 8)).decode())["id"]
            self.p.save(root)
            self.assertTrue((root / "snapshots" / f"{vp}.png").is_file())
            opened = Project.open(root)
            self.assertEqual(opened.sheet_data(self.sheet)["viewports"][0]["image"]["width_px"], 8)
            self.assertEqual(opened.warnings, [])
            self.p.execute("sheet.remove_viewport", {"id": self.sheet, "viewport": vp})
            self.p.save(root)
            self.assertFalse((root / "snapshots" / f"{vp}.png").exists())
            self.p.undo()                                                                    # undo brings the viewport back with its picture
            self.assertEqual(len(self.vps()), 1)
            self.p.save(root)
            self.assertTrue((root / "snapshots" / f"{vp}.png").is_file())
            (root / "snapshots" / f"{vp}.png").unlink()
            lost = Project.open(root)
            self.assertEqual(lost.sheet_data(self.sheet)["viewports"], [])
            self.assertTrue(any("3D snapshot" in w for w in lost.warnings))

    def test_a_report_is_set_in_a_box_and_read_back_from_the_stored_report(self):
        rid = self.run_("report.create", name="Status", sections=["summary", "levels"])["id"]
        vp = self.run_("sheet.place_report", id=self.sheet, report=rid, width=120, size=2.5, lines=40)["id"]
        box = self.vps()[0]["report"]
        self.assertEqual(box["name"], "Status")
        styles = {l["style"] for l in box["lines"]}
        self.assertTrue({"h1", "row", "head"} <= styles)
        self.assertLessEqual(len(box["lines"]), 40)
        texts = [c[1] for l in box["lines"] for c in l.get("cells", [])]
        self.assertIn("Level 01", texts)
        self.assertEqual(box["hidden"], 0)
        # a short box says how much is not shown instead of silently dropping it
        self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="lines", value=5)
        short = self.vps()[0]["report"]
        self.assertEqual(len(short["lines"]), 5)
        self.assertGreater(short["hidden"], 0)
        self.assertIn("more line(s) are in the report", short["lines"][-1]["text"])
        with self.assertRaises(BadRequest):
            self.run_("sheet.set_viewport", id=self.sheet, viewport=vp, key="lines", value=2)

    def test_a_placed_report_cannot_be_deleted_and_stays_current_until_remade(self):
        rid = self.run_("report.create", name="Status", sections=["summary"])["id"]
        vp = self.run_("sheet.place_report", id=self.sheet, report=rid)["id"]
        self.run_("sheet.issue", id=self.sheet, description="First issue")
        self.wall(a=(0, 3000), b=(0, 6000))
        self.assertEqual(self.p.sheet_data(self.sheet)["status"]["state"], "current")          # the report is a snapshot of the project, so the sheet shows it unchanged
        with self.assertRaises(Conflict):
            self.run_("report.delete", id=rid)
        self.run_("sheet.remove_viewport", id=self.sheet, viewport=vp)
        self.run_("report.delete", id=rid)
        with self.assertRaises(Exception):
            self.run_("sheet.place_report", id=self.sheet, report=rid)

    def test_every_kind_is_in_the_model_check_without_findings_and_the_title_block_scale_ignores_them(self):
        rid = self.run_("report.create", name="Status", sections=["summary"])["id"]
        self.run_("sheet.place_view", id=self.sheet, view_id=self.view3d, scale="1:200")
        self.run_("sheet.place_snapshot", id=self.sheet, data=base64.b64encode(png(8, 8)).decode())
        self.run_("sheet.place_report", id=self.sheet, report=rid)
        self.assertEqual(self.p.sheet_data(self.sheet)["title_block"]["scale"], "1:200")
        messages = [i["message"] for i in self.p.check()["issues"] if i.get("category") == "references"]
        self.assertEqual(messages, [])
        rows = [r for g in self.p.properties({"sheet": self.sheet})["groups"] for r in g["rows"] if str(r.get("key", "")).startswith("vp:")]
        self.assertEqual(len(rows), (3 + 1 + 3) + 3 * 3)                                         # model: scale, azimuth, elevation; snapshot: width; report: width, size, lines; and x, y, position for each of the three


if __name__ == "__main__":
    unittest.main()
