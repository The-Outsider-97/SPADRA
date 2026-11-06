"""Sheets arrange themselves: new viewports land in free places, Arrange gives every viewport a clear place without moving what the person fixed, and a
sheet that cannot hold everything says so instead of piling things up."""
from __future__ import annotations

import unittest

from applications.SPADRA import sheetviews as SV
from applications.tools.errors import BadRequest, Conflict

from .harness import ServerCase
from .test_bim import Case


class Layout(Case):
    def setUp(self) -> None:
        super().setUp()
        for i in range(4):
            self.wall(a=(0, i * 4000), b=(6000, i * 4000))
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        self.sheet = self.run_("sheet.create", name="Overview")["id"]

    def sh(self):
        return next(s for s in self.p.manifest["sheets"] if s["id"] == self.sheet)

    def rects(self):
        return {vp["id"]: SV.rect_of(self.p, vp) for vp in self.sh()["viewports"]}

    def place(self, **extra):
        return self.run_("sheet.place_view", id=self.sheet, view_id=self.plan, **extra)

    def no_overlap(self):
        self.assertEqual(SV.overlapping(self.p, self.sh()), [])
        width, height = self._paper()
        for r in self.rects().values():
            self.assertGreaterEqual(r[0], SV.FRAME)
            self.assertGreaterEqual(r[1], SV.FRAME)
            self.assertLessEqual(r[2], width - SV.FRAME)
            self.assertLessEqual(r[3], height - SV.FRAME)
        for furniture in SV.furniture(self.sh(), width, height):
            for r in self.rects().values():
                self.assertFalse(SV._hits(r, furniture), (r, furniture))

    def _paper(self):
        from applications.SPADRA.project import paper_size
        return paper_size(self.sh()["paper"])


class NewViewports(Layout):
    def test_all_a_series_orientations_and_custom_sizes_are_real_sheet_dimensions(self):
        from applications.SPADRA.project import paper_size
        for size in range(5):
            for orientation in ("Portrait", "Landscape"):
                name = f"A{size} {orientation}"
                self.run_("sheet.set", id=self.sheet, key="paper", value=name)
                dims = paper_size(name)
                self.assertEqual((self.p.sheet_data(self.sheet)["width"], self.p.sheet_data(self.sheet)["height"]), dims)
        self.run_("sheet.set", id=self.sheet, key="paper", value="Custom 900x610 mm")
        self.assertEqual((self.p.sheet_data(self.sheet)["width"], self.p.sheet_data(self.sheet)["height"]), (900, 610))
        with self.assertRaises(BadRequest):
            self.run_("sheet.set", id=self.sheet, key="paper", value="Custom 100x120 mm")

    def test_new_viewports_land_in_free_places_and_never_pile_up(self):
        out = [self.place(scale="1:100") for _ in range(4)]
        self.assertFalse(any(o["crowded"] for o in out))
        self.no_overlap()
        self.assertEqual(len(self.rects()), 4)

    def test_placing_a_viewport_moves_nothing_that_is_already_there(self):
        self.place(scale="1:100")
        before = dict(self.rects())
        self.place(scale="1:100")
        for vid, r in before.items():
            self.assertEqual(self.rects()[vid], r)

    def test_an_explicit_position_is_respected_and_remembered_as_manual(self):
        out = self.place(scale="1:100", x=200, y=100)
        vp = self.sh()["viewports"][0]
        self.assertEqual((vp["x"], vp["y"], vp["manual"], out["crowded"]), (200, 100, True, False))

    def test_a_full_sheet_reports_crowding_instead_of_hiding_it(self):
        self.run_("sheet.set", id=self.sheet, key="paper", value="A4 Portrait")
        flags = [self.place(scale="1:20")["crowded"] for _ in range(3)]
        self.assertTrue(any(flags))


class Arrange(Layout):
    def test_arranging_a_pile_gives_every_viewport_a_clear_place(self):
        for _ in range(4):
            self.place(scale="1:100", x=40, y=40)                                           # four on top of each other, as before
        self.assertGreater(len(SV.overlapping(self.p, self.sh())), 0)
        out = self.run_("sheet.arrange", id=self.sheet, mode="all")
        self.assertEqual(len(out["moved"]), 4)
        self.no_overlap()

    def test_arrange_keeps_scale_and_shape_and_is_repeatable(self):
        for _ in range(3):
            self.place(scale="1:50", x=40, y=40)
        sizes = {vp["id"]: SV.footprint(self.p, vp)[:2] for vp in self.sh()["viewports"]}
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        first = {k: (v["x"], v["y"]) for k, v in ((vp["id"], vp) for vp in self.sh()["viewports"])}
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        again = {vp["id"]: (vp["x"], vp["y"]) for vp in self.sh()["viewports"]}
        self.assertEqual(first, again)
        self.assertEqual(sizes, {vp["id"]: SV.footprint(self.p, vp)[:2] for vp in self.sh()["viewports"]})
        self.assertEqual({vp["scale"] for vp in self.sh()["viewports"]}, {"1:50"})

    def test_a_locked_viewport_and_a_hand_placed_one_stay_put_in_auto_mode(self):
        pinned = self.place(scale="1:100", x=60, y=60)["id"]
        self.run_("sheet.set_viewport", id=self.sheet, viewport=pinned, key="locked", value=True)
        by_hand = self.place(scale="1:100", x=300, y=200)["id"]
        loose = self.place(scale="1:100")["id"]
        self.run_("sheet.arrange", id=self.sheet)
        by_id = {vp["id"]: vp for vp in self.sh()["viewports"]}
        self.assertEqual((by_id[pinned]["x"], by_id[pinned]["y"]), (60, 60))
        self.assertEqual((by_id[by_hand]["x"], by_id[by_hand]["y"]), (300, 200))
        self.assertTrue(by_id[loose])
        self.no_overlap_or_only_fixed(pinned, by_hand)

    def no_overlap_or_only_fixed(self, *fixed):
        for a, b in SV.overlapping(self.p, self.sh()):
            self.assertIn(a, fixed)
            self.assertIn(b, fixed)

    def test_mode_all_moves_hand_placed_viewports_but_never_locked_ones(self):
        pinned = self.place(scale="1:100", x=60, y=60)["id"]
        self.run_("sheet.set_viewport", id=self.sheet, viewport=pinned, key="locked", value=True)
        by_hand = self.place(scale="1:100", x=60, y=60)["id"]
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        by_id = {vp["id"]: vp for vp in self.sh()["viewports"]}
        self.assertEqual((by_id[pinned]["x"], by_id[pinned]["y"]), (60, 60))
        self.assertNotEqual((by_id[by_hand]["x"], by_id[by_hand]["y"]), (60, 60))
        self.assertEqual(SV.overlapping(self.p, self.sh()), [])

    def test_a_locked_viewport_cannot_be_dragged(self):
        vid = self.place(scale="1:100")["id"]
        self.run_("sheet.set_viewport", id=self.sheet, viewport=vid, key="locked", value=True)
        with self.assertRaises(Conflict):
            self.run_("sheet.set_viewport", id=self.sheet, viewport=vid, key="position", value=[100, 100])

    def test_what_cannot_fit_is_reported_and_nothing_moves(self):
        self.run_("sheet.set", id=self.sheet, key="paper", value="A4 Portrait")
        for _ in range(3):
            self.place(scale="1:10", x=20, y=20)
        before = [(v["x"], v["y"]) for v in self.sh()["viewports"]]
        with self.assertRaises(Conflict) as caught:
            self.run_("sheet.arrange", id=self.sheet, mode="all")
        self.assertIn("do not fit", str(caught.exception))
        self.assertEqual(before, [(v["x"], v["y"]) for v in self.sh()["viewports"]])

    def test_rescale_is_the_only_way_a_scale_changes_and_it_is_reported(self):
        self.run_("sheet.set", id=self.sheet, key="paper", value="A3 Landscape")
        for _ in range(2):
            self.place(scale="1:20", x=20, y=20)
        out = self.run_("sheet.arrange", id=self.sheet, mode="all", rescale=True)
        self.assertTrue(out["rescaled"])
        self.assertEqual(SV.overlapping(self.p, self.sh()), [])

    def test_the_title_block_and_revision_table_are_kept_clear(self):
        for _ in range(4):
            self.place(scale="1:100", x=30, y=30)
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        self.no_overlap()

    def test_arrange_is_one_undo_step(self):
        for _ in range(3):
            self.place(scale="1:100", x=30, y=30)
        before = [(v["x"], v["y"]) for v in self.sh()["viewports"]]
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        self.p.undo()
        self.assertEqual(before, [(v["x"], v["y"]) for v in self.sh()["viewports"]])

    def test_the_viewport_still_points_at_its_view_after_arranging(self):
        for _ in range(2):
            self.place(scale="1:100", x=30, y=30)
        self.run_("sheet.arrange", id=self.sheet, mode="all")
        self.assertEqual({v["view_id"] for v in self.sh()["viewports"]}, {self.plan})
        self.assertEqual(len(self.p.sheet_data(self.sheet)["viewports"]), 2)


class Alignment(Layout):
    def test_viewports_align_to_the_first_and_snap_to_edges(self):
        a = self.place(scale="1:100")["id"]
        b = self.place(scale="1:100")["id"]
        self.run_("sheet.align", id=self.sheet, viewports=[a, b], how="left")
        rects = self.rects()
        self.assertAlmostEqual(rects[a][0], rects[b][0], places=3)
        vp = next(v for v in self.sh()["viewports"] if v["id"] == b)
        dx = SV.footprint(self.p, vp)[2]
        near = rects[a][2] + 1.5 - dx                                                          # dropped so that its left edge is 1.5 mm from the first's right edge
        x, _ = SV.snap(self.p, self.sh(), vp, near, vp["y"])
        self.assertAlmostEqual(SV.rect_of(self.p, {**vp, "x": x})[0], rects[a][2], places=3)

    def test_unknown_alignment_and_too_few_viewports_are_refused(self):
        a = self.place(scale="1:100")["id"]
        with self.assertRaises(BadRequest):
            self.run_("sheet.align", id=self.sheet, viewports=[a], how="left")
        with self.assertRaises(BadRequest):
            self.run_("sheet.align", id=self.sheet, viewports=[a, a], how="diagonal")

    def test_reordering_changes_what_is_drawn_on_top(self):
        a = self.place(scale="1:100")["id"]
        b = self.place(scale="1:100")["id"]
        out = self.run_("sheet.order_viewport", id=self.sheet, viewport=a, to="front")
        self.assertEqual(out["order"], [b, a])


if __name__ == "__main__":
    unittest.main()


class Export(Layout):
    """A sheet leaves as .sldc and comes back with everything it holds; 'preserved' is a comparison, not a claim."""

    def build(self):
        from .test_sheet_contents import png
        self.run_("model.add_door", wall=next(iter(self.p.model.object_ids())) if False else self.wall(a=(0, 20000), b=(6000, 20000)), offset=2.0)
        self.run_("sheet.set", id=self.sheet, key="drawn_by", value="Alejandro")
        self.run_("sheet.add_note", id=self.sheet, text="Check on site", x=30, y=30)
        a = self.place(scale="1:100")["id"]
        view3d = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "3d")
        b = self.run_("sheet.place_view", id=self.sheet, view_id=view3d, scale="1:200")["id"]
        c = self.run_("sheet.place_snapshot", id=self.sheet, data=__import__("base64").b64encode(png(30, 20)).decode(), title="Picture")["id"]
        return a, b, c

    def export(self):
        from applications.SPADRA import sheets as SH
        return SH.to_sldc(self.p, self.sheet)

    def test_the_file_is_a_valid_sldc_document_a_person_can_read(self):
        from src.utils import sldc as S
        self.build()
        data, name = self.export()
        self.assertTrue(name.endswith(".sldc"))
        package = S.loads_package(data)
        text = S.to_markdown(package)
        for needle in ("Title block", "Alejandro", "Viewports", "1:100", "Check on site"):
            self.assertIn(needle, text)
        self.assertEqual(len(package.assets), 1)                                       # the 3D picture, byte for byte

    def test_graphical_sheet_export_keeps_the_a1_page_and_editable_viewport_structure(self):
        from applications.SPADRA import sheets as SH
        from src.utils import sldc as S
        from .test_sheet_contents import png
        viewport = self.place(scale="1:100")["id"]
        sheet_png = png(841, 594)
        data, _ = SH.to_sldc(self.p, self.sheet, sheet_png)
        pkg = S.loads_package(data)
        section = pkg.document["sections"][0]
        self.assertEqual(section["page"]["width_mm"], 841.0)
        self.assertEqual(section["page"]["height_mm"], 594.0)
        figure = pkg.document["blocks"][0]
        self.assertEqual(figure["type"], "figure")
        self.assertEqual(figure["section"], section["id"])
        self.assertEqual(figure["figure"]["width_mm"], 841.0)
        self.assertEqual(figure["figure"]["height_mm"], 594.0)
        self.assertEqual(pkg.assets[figure["figure"]["asset"]].data, sheet_png)
        self.assertEqual(SH.read_sldc(data)["sheet"]["viewports"][0]["id"], viewport)
        self.assertTrue(SH.verify_sldc(self.p, data, self.sheet)["identical"])
        imported = self.run_("sheet.import", data=__import__("base64").b64encode(data).decode())["id"]
        reopened = self.p.sheet_data(imported)
        self.assertEqual((reopened["width"], reopened["height"]), (841.0, 594.0))
        self.assertEqual([(v["x"], v["y"], v["scale"]) for v in reopened["viewports"]],
                         [(v["x"], v["y"], v["scale"]) for v in self.p.sheet_data(self.sheet)["viewports"]])

    def test_graphical_export_retains_real_svg_vectors_with_a_verified_page_size(self):
        from applications.SPADRA import sheets as SH
        from src.utils import sldc as S
        from .test_sheet_contents import png
        self.place(scale="1:100")
        # A concrete vector drawing: not a text-only description or a raster-only package.
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 841 594" '
               'width="3179" height="2245"><path d="M40 40L800 40L800 550Z" '
               'fill="none" stroke="#111"/><text x="50" y="65">A102</text></svg>')
        package_bytes, _ = SH.to_sldc(self.p, self.sheet, png(841, 594), svg)
        package = S.loads_package(package_bytes)
        saved = SH.read_sldc(package_bytes)
        self.assertEqual(saved["vector_svg"], svg)
        self.assertEqual(saved["sheet"]["width"], 841)
        self.assertEqual(saved["sheet"]["height"], 594)
        self.assertEqual(package.document["sections"][0]["page"]["width_mm"], 841)
        self.assertIn("<path", saved["vector_svg"])
        self.assertTrue(SH.verify_sldc(self.p, package_bytes, self.sheet)["identical"])

    def test_graphical_export_rejects_wrong_sized_or_unsafe_svg(self):
        from applications.SPADRA import sheets as SH
        from .test_sheet_contents import png
        valid_png = png(841, 594)
        for xml in (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 210 297"/>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 841 594"><script>alert(1)</script></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 841 594"><image href="https://example.com/x.png"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 841 594"><path style="fill:url(https://example.com/gradient)"/></svg>',
            'not SVG',
        ):
            with self.subTest(xml=xml[:80]), self.assertRaises(BadRequest):
                SH.to_sldc(self.p, self.sheet, valid_png, xml)

    def test_vector_page_can_be_independently_rendered_to_an_a1_pdf(self):
        """Independent Inkscape/PDF parser check; does not substitute for the live browser comparison."""
        import io
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from applications.SPADRA import sheets as SH
        from .test_sheet_contents import png
        if not shutil.which("inkscape"):
            self.skipTest("Inkscape is unavailable for independent vector PDF rendering")
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("pypdf is unavailable for independent PDF page measurement")
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="3179.52756" height="2245.03937" '
               'viewBox="0 0 841 594"><rect x="15" y="15" width="811" height="564" '
               'fill="white" stroke="black"/><path d="M100 100L500 100L500 430Z" '
               'stroke="black" fill="none"/><text x="120" y="140">SECTION A-A</text></svg>')
        out, _ = SH.to_sldc(self.p, self.sheet, png(841, 594), svg)
        preserved = SH.read_sldc(out)["vector_svg"]
        with tempfile.TemporaryDirectory(prefix="spadra-sheet-parity-") as directory:
            pdf = Path(directory) / "A1.pdf"
            proc = subprocess.run(["inkscape", "--pipe", f"--export-filename={pdf}"], input=preserved.encode(), capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
            page = PdfReader(io.BytesIO(pdf.read_bytes())).pages[0]
            self.assertAlmostEqual(float(page.mediabox.width) * 25.4 / 72, 841, delta=0.35)
            self.assertAlmostEqual(float(page.mediabox.height) * 25.4 / 72, 594, delta=0.35)
            self.assertGreater(len(page.get_contents().get_data()), 150, "the PDF must contain drawn content")

    def test_sheet_export_refuses_a_preview_with_incorrect_page_proportions(self):
        from applications.SPADRA import sheets as SH
        from .test_sheet_contents import png
        with self.assertRaises(BadRequest):
            SH.to_sldc(self.p, self.sheet, png(210, 297))

    def test_reading_the_file_back_finds_no_difference_from_the_sheet(self):
        from applications.SPADRA import sheets as SH
        self.build()
        data, _ = self.export()
        report = SH.verify_sldc(self.p, data, self.sheet)
        self.assertEqual(report["differences"], [])
        self.assertTrue(report["identical"])
        self.assertEqual(report["viewports"], 3)

    def test_a_later_change_to_the_sheet_is_a_difference_the_check_names(self):
        from applications.SPADRA import sheets as SH
        a, _, _ = self.build()
        data, _ = self.export()
        self.run_("sheet.set_viewport", id=self.sheet, viewport=a, key="x", value=111)
        report = SH.verify_sldc(self.p, data, self.sheet)
        self.assertFalse(report["identical"])
        self.assertTrue(any("'x' differs" in d for d in report["differences"]), report["differences"])

    def test_layout_locks_and_order_survive_the_round_trip(self):
        from applications.SPADRA import sheets as SH
        a, b, c = self.build()
        self.run_("sheet.set_viewport", id=self.sheet, viewport=b, key="locked", value=True)
        self.run_("sheet.order_viewport", id=self.sheet, viewport=a, to="front")
        data, _ = self.export()
        got = SH.read_sldc(data)
        self.assertEqual(got["order"], [v["id"] for v in self.sh()["viewports"]])
        self.assertEqual({v["id"]: v["locked"] for v in got["sheet"]["viewports"]}, {a: False, b: True, c: False})
        self.assertTrue(SH.verify_sldc(self.p, data, self.sheet)["identical"])

    def test_importing_makes_a_new_sheet_with_the_same_layout_and_picture(self):
        a, b, c = self.build()
        self.run_("sheet.set_viewport", id=self.sheet, viewport=a, key="locked", value=True)
        data, _ = self.export()
        out = self.run_("sheet.import", data=__import__("base64").b64encode(data).decode())
        self.assertEqual((out["viewports"], out["skipped"]), (3, []))
        new = next(s for s in self.p.manifest["sheets"] if s["id"] == out["id"])
        old = self.sh()
        self.assertNotEqual(new["number"], old["number"])
        for x, y in zip(old["viewports"], new["viewports"]):
            self.assertEqual((x.get("view_id"), x["x"], x["y"], x.get("scale"), bool(x.get("locked"))), (y.get("view_id"), y["x"], y["y"], y.get("scale"), bool(y.get("locked"))))
        self.assertEqual(self.p.snapshots[new["viewports"][2]["id"]], self.p.snapshots[old["viewports"][2]["id"]])
        self.assertEqual(self.p.sheet_data(new["id"])["title_block"]["drawn_by"], "Alejandro")
        self.assertEqual(self.p.sheet_data(new["id"])["notes"][0]["text"], "Check on site")

    def test_a_viewport_whose_view_is_not_in_the_project_is_listed_not_guessed(self):
        self.build()
        data, _ = self.export()
        other = type(self.p).create("Other")
        import base64
        out = other.execute("sheet.import", {"data": base64.b64encode(data).decode()})["result"]
        self.assertEqual(out["viewports"], 1)                                          # only the picture travels
        self.assertEqual(len(out["skipped"]), 2)
        self.assertTrue(all("not in this project" in s["why"] for s in out["skipped"]))

    def test_a_file_that_is_not_a_sheet_is_refused(self):
        from applications.SPADRA import reports as RP
        from applications.SPADRA import sheets as SH
        entry_data, _ = RP.build(self.p, "R", ["summary"])
        with self.assertRaises(BadRequest):
            SH.read_sldc(entry_data)
        with self.assertRaises(BadRequest):
            SH.read_sldc(b"not a file")


class OverHttp(ServerCase):
    def test_the_sheet_downloads_as_sldc_and_verifies_against_itself(self):
        from src.utils import sldc
        from .test_sheet_contents import png
        import base64
        a = self.api
        a.post("/api/project/new", {"name": "S", "unit": "mm"})
        sid = a.cmd("sheet.create", {"name": "Plan"})["result"]["id"]
        self.assertEqual(a.call("GET", f"/api/export/sheet/{sid}")[0], 409)
        project_revision = a.get("/api/project/tree")["project"]["revision"]
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 841 594"><path d="M20 20L820 20"/></svg>'
        result = a.post(f"/api/export/sheet/{sid}", {"preview_png": base64.b64encode(png(841, 594)).decode(), "preview_svg": svg, "revision": project_revision})
        raw = base64.b64decode(result["data"])
        package = sldc.loads_package(raw)
        self.assertEqual(package.document["extensions"]["spadra.sheet"]["format"], "spadra.sheet.v1")
        self.assertEqual(package.document["sections"][0]["page"]["width_mm"], 841.0)
        self.assertEqual(package.document["blocks"][0]["type"], "figure")
        self.assertEqual(package.document["extensions"]["spadra.sheet"]["vector_svg"], svg)
        check = a.get(f"/api/export/sheet/{sid}/verify")
        self.assertEqual((check["identical"], check["differences"]), (True, []))
        self.assertEqual(a.call("GET", "/api/export/sheet/nope?legacy_text=1")[0], 404)
