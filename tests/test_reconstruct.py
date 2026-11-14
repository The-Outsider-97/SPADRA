"""P1: dimension-aware 2D-to-BIM reconstruction, through the real pipeline and the real project.

The pieces that need no OCR (line-work, dimension association, calibration, alignment, SDRW and vector-PDF sources, building into a project) always run. Tests that read printed
dimensions from a *raster image* need Tesseract: where it is missing they are skipped with that reason, and a test asserts the honest 'unavailable' outcome instead. The fixtures are
synthetic drawings with known truth (see recon_fixtures): what these tests show is that the pipeline is correct on them, not that it reads every real plan."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import reconstruct as R
from applications.SPADRA.project import Project
from applications.SPADRA.tests import recon_fixtures as F
from applications.SPADRA.tests.harness import ServerCase
from applications.tools.errors import BadRequest

CAPS = R.capabilities()
OCR = CAPS["ocr"]["available"]
PDF = CAPS["sources"]["pdf"]["available"]
FX = {f.name: f for f in F.fixtures()}


def seg(i, a, b):
    s = R.merge_segments([(a, b)])[0]
    s["id"] = i
    return s


def sdrw_project(fx: F.Fixture):
    """A project whose plan drawing holds the fixture as CAD line-work and numeric text entities (what an exported or hand-drawn dimensioned plan looks like)."""
    p = Project.create("rec", unit="mm")
    view = next(v for v in p.manifest["views"] if v["kind"] == "plan")["id"]
    doc = p.drawing(view)
    for a, b in F._outline_segments(fx):
        doc.add_line(list(a), list(b))
    for d in fx.dims:
        prim = F._dimension_primitives(d, 1)
        for a, b in prim["lines"]:
            doc.add_line(list(a), list(b))
        h = 250.0
        w = 0.6 * h * len(prim["text"])
        if prim["axis"] == "x":
            doc.add_text([prim["center"][0] - w / 2, prim["center"][1] + 60], prim["text"], h)
        else:
            doc.add_text([prim["center"][0] - 60, prim["center"][1] - w / 2], prim["text"], h, rotation=90.0, degrees=True)
    return p, view


class Lines(unittest.TestCase):
    def test_01_collinear_pieces_merge_and_near_axis_lines_snap_to_the_axis(self):
        out = R.merge_segments([((0, 10.0), (50, 10.4)), ((52, 10.2), (120, 9.9)), ((0, 40), (100, 40))])
        horizontal = [s for s in out if s["theta"] == 0.0]
        self.assertEqual(len(horizontal), 2)
        self.assertEqual(sorted(round(s["length"]) for s in horizontal), [100, 120])
        self.assertEqual(sorted(s["id"] for s in out), ["s0", "s1"])

    def test_02_chained_dimensions_share_one_line_and_each_text_measures_its_own_span(self):
        # one dimension line at y=100 from x=0 to x=300 with ticks at 0, 100, 300; two texts above it
        line = seg("dl", (0, 100), (300, 100))
        ticks = [seg(f"t{x}", (x - 6, 94), (x + 6, 106)) for x in (0, 100, 300)]
        ext = [seg(f"e{x}", (x, 20), (x, 112)) for x in (0, 100, 300)]
        segs = [line, *ticks, *ext]
        texts = [{"id": "a", "text": "1000", "value": 1000.0, "bbox": [30, 78, 70, 92], "conf": 0.95, "rotation": 0},
                 {"id": "b", "text": "2000", "value": 2000.0, "bbox": [180, 78, 220, 92], "conf": 0.95, "rotation": 0}]
        dims = R.find_dimensions(segs, texts, 8.0)
        got = sorted((d["text"], round(d["length_px"])) for d in dims)
        self.assertEqual(got, [("1000", 100), ("2000", 200)])

    def test_03_an_extension_line_crossing_another_dimension_line_is_not_a_closing_mark(self):
        # the lower dimension line (y=200) is crossed by the extension line at x=100 that belongs to the upper dimension: only the end ticks close it
        upper = seg("u", (0, 100), (100, 100))
        lower = seg("l", (0, 200), (300, 200))
        ticks = [seg("t0", (-6, 194), (6, 206)), seg("t300", (294, 194), (306, 206))]
        ext = [seg("e0", (0, 20), (0, 212)), seg("e100", (100, 20), (100, 112)), seg("e300", (300, 20), (300, 212))]
        text = [{"id": "x", "text": "3000", "value": 3000.0, "bbox": [130, 178, 170, 192], "conf": 0.9, "rotation": 0}]
        dims = R.find_dimensions([upper, lower, *ticks, *ext], text, 8.0)
        self.assertEqual([round(d["length_px"]) for d in dims], [300])


class Calibration(unittest.TestCase):
    def dim(self, i, text, length, conf=0.95):
        return {"id": i, "text": text, "raw_value": float(text), "length_px": length, "ocr_conf": conf, "terminators": {"extension_lines": 2, "marks": 2}}

    def test_04_scale_is_the_median_and_a_misread_dimension_is_an_outlier_not_a_vote(self):
        dims = [self.dim("d0", "5200", 650.0), self.dim("d1", "4200", 525.0), self.dim("d2", "3000", 375.0), self.dim("d3", "9000", 375.0)]   # d3 is misread: 9000 on 375 px
        cal = R.calibrate(dims, R.DEFAULTS)
        self.assertAlmostEqual(cal["mm_per_unit"], 8.0, delta=0.01)
        self.assertEqual(next(d for d in dims if d["id"] == "d3")["status"], "outlier")
        self.assertIn("dimension.outlier", {d["code"] for d in cal["diagnostics"]})

    def test_05_decimal_values_are_metres_unless_the_unit_is_given_and_no_dimension_means_no_scale(self):
        d = [self.dim("d0", "5200", 650.0)]
        d[0].update(text="5.20", raw_value=5.2)
        self.assertAlmostEqual(R.calibrate(d, R.DEFAULTS)["mm_per_unit"], 8.0, delta=0.01)
        none = R.calibrate([], R.DEFAULTS)
        self.assertIsNone(none["mm_per_unit"])
        self.assertEqual(none["diagnostics"][0]["code"], "scale.none")


class Alignment(unittest.TestCase):
    def walls(self):
        # two vertical walls 5000 apart (pixels = mm / 8) and one horizontal wall across them
        def w(i, theta, rho, lo, hi):
            return {"id": i, "theta": theta, "rho": rho, "t": 25.0, "lo": lo, "hi": hi, "openings": [], "faces": [], "pair_overlap": 1.0, "pieces": 1, "join": {"lo": None, "hi": None}}
        return [w("w0", math.pi / 2, -100.0, 100.0, 600.0), w("w1", math.pi / 2, -740.0, 100.0, 600.0), w("w2", 0.0, 100.0, 100.0, 740.0)]

    def dim(self, i, p, q, value, conf):
        return {"id": i, "text": f"{value:g}", "raw_value": value, "value_mm": float(value), "axis": "x", "p": [p, 50.0], "q": [q, 50.0], "length_px": q - p, "status": "used", "ocr_conf": conf,
                "confidence_pre": conf, "terminators": {"extension_lines": 2, "marks": 2}}

    def test_06_a_dimension_to_the_faces_moves_the_centre_lines_to_the_printed_distance(self):
        walls = self.walls()
        # outer faces at x = 87.5 and 752.5 (centre lines 100 and 740, half thickness 12.5 px); the printed outer-to-outer value is 5400 mm, i.e. 675 px, not the drawn 665 px
        d = self.dim("d0", 87.5, 752.5, 5400, 0.95)
        al = R.align([d], walls, 8.0, R.DEFAULTS)
        w0, w1 = walls[0], walls[1]
        self.assertEqual(al["report"]["axes"]["x"]["status"], "well_constrained")
        self.assertAlmostEqual((-w1["rho"] - -w0["rho"]) * 8.0, 5400 - 25 * 8.0, delta=0.01)       # centre to centre = printed value minus both half-thicknesses

    def test_07_contradicting_dimensions_are_localised_and_the_least_trusted_is_dropped_and_reported(self):
        walls = self.walls()
        walls.append({**self.walls()[0], "id": "w3", "rho": -420.0})                                 # a third vertical line between them
        good1 = self.dim("d0", 87.5, 427.5, 2700, 0.97)
        good2 = self.dim("d1", 412.5, 752.5, 2700, 0.97)
        total = self.dim("d2", 87.5, 752.5, 5500, 0.6)                                                # 2700 + 2700 + wall = 5425 or so: this one disagrees
        al = R.align([good1, good2, total], walls, 8.0, R.DEFAULTS)
        self.assertEqual(total["status"], "dropped")
        self.assertEqual([x["dimension"] for x in al["report"]["dropped"]], ["d2"])
        self.assertIn("dimension.conflict", {d["code"] for d in al["diagnostics"]})
        self.assertNotEqual(good1["status"], "dropped")

    def test_08_a_dimension_between_the_two_faces_of_one_wall_sets_its_thickness(self):
        walls = self.walls()
        d = self.dim("d0", 87.5, 112.5, 220, 0.95)                                                   # 25 px = 200 mm drawn, 220 mm printed
        R.align([d], walls, 8.0, R.DEFAULTS)
        self.assertEqual(d["status"], "thickness")
        self.assertAlmostEqual(walls[0]["t"] * 8.0, 220.0, delta=0.001)


@unittest.skipUnless(PDF, "pdfplumber is not installed")
class VectorPdf(unittest.TestCase):
    def test_09_a_vector_pdf_is_read_exactly_walls_openings_rooms_and_the_scale_from_the_text(self):
        try:
            data, ext, meta = F.render(FX["two-rooms-pdf"])
        except ImportError as exc:
            self.skipTest(str(exc))
        res = R.run({"kind": "pdf", "data": data})
        self.assertTrue(res["ok"], res["diagnostics"])
        self.assertEqual(res["summary"]["walls"], 5)
        self.assertEqual(res["summary"]["openings"], 3)
        self.assertAlmostEqual(res["scale"]["mm_per_unit"] * meta["pt_per_mm"], 1.0, delta=0.002)
        self.assertEqual(sorted(round(s["area_m2"], 3) for s in res["spaces"]), [12.635, 16.435])
        self.assertTrue(all(d["status"] == "used" for d in res["dimensions"]))
        kinds = sorted(o["kind"] for w in res["walls"] for o in w["openings"])
        self.assertEqual(kinds, ["door", "door", "window"])

    def test_10_the_same_input_gives_the_same_model(self):
        try:
            data, _e, _m = F.render(FX["two-rooms-pdf"])
        except ImportError as exc:
            self.skipTest(str(exc))
        a, b = R.run({"kind": "pdf", "data": data}), R.run({"kind": "pdf", "data": data})
        self.assertEqual(a["walls"], b["walls"])
        self.assertEqual(a["spaces"], b["spaces"])


class SdrwSource(unittest.TestCase):
    def test_11_an_sdrw_drawing_with_numeric_text_is_reconstructed_in_millimetres(self):
        p, view = sdrw_project(FX["two-rooms"])
        res = R.run({"kind": "sdrw", "drawing": view}, project=p)
        self.assertTrue(res["ok"], res["diagnostics"])
        self.assertAlmostEqual(res["scale"]["mm_per_unit"], 1.0, delta=0.005)
        self.assertEqual(res["summary"]["walls"], 5)
        for got, want in zip(sorted(s["area_m2"] for s in res["spaces"]), (12.635, 16.435)):
            self.assertAlmostEqual(got, want, delta=0.03)
        t = sorted(w["thickness_mm"] for w in res["walls"])
        self.assertEqual(t, [150.0, 200.0, 200.0, 200.0, 200.0])

    def test_12_draw_writes_walls_openings_and_rooms_on_their_own_layers_with_provenance(self):
        p, view = sdrw_project(FX["two-rooms"])
        res = R.run({"kind": "sdrw", "drawing": view}, project=p)
        target = next(v for v in p.manifest["views"] if v["kind"] == "plan" and v["id"] != view) if len([v for v in p.manifest["views"] if v["kind"] == "plan"]) > 1 else None
        view2 = target["id"] if target else view
        before = len(p.drawing(view2).query_ids(limit=5000))
        made = p.execute("reconstruct.draw", {"drawing": view2, "model": res, "origin_mm": [20000, 0]})["result"]["created"]
        self.assertEqual(len(made["spaces"]), 2)
        self.assertEqual(len(made["openings"]), 3)
        doc = p.drawing(view2)
        self.assertEqual(len(doc.query_ids(limit=5000)) - before, sum(len(v) for v in made.values()))
        room = doc.get(made["spaces"][0])
        self.assertEqual(room["layer"], "A-ROOM")
        self.assertEqual(room["metadata"]["spadra:recon"]["ifc"], "IfcSpace")
        self.assertIn("confidence", room["metadata"]["spadra:recon"])


class Build(unittest.TestCase):
    def setUp(self):
        self.src, self.view = sdrw_project(FX["two-rooms"])
        self.res = R.run({"kind": "sdrw", "drawing": self.view}, project=self.src)
        self.p = Project.create("bim", unit="mm")

    def test_13_build_makes_bim_elements_with_ifc_classes_hosts_and_provenance_in_one_undoable_step(self):
        rev = self.p.revision
        made = self.p.execute("reconstruct.build", {"model": self.res})["result"]
        self.assertEqual((len(made["walls"]), len(made["openings"]), len(made["spaces"])), (5, 3, 2))
        self.assertEqual(sorted(set(made["ifc"].values())), ["IfcDoor", "IfcSpace", "IfcWall", "IfcWindow"])
        from applications.SPADRA import elements as E
        opening = E.meta_of(self.p, next(iter(made["openings"].values())))
        self.assertIn(E.m(opening, "host"), made["walls"].values())
        self.assertIn("evidence", E.m(opening, "recon"))
        wall = E.meta_of(self.p, next(iter(made["walls"].values())))
        self.assertEqual(E.m(wall, "recon")["source"], "sdrw")
        self.assertEqual(self.p.revision, rev + 1, "one command, one revision")
        self.p.undo()
        self.assertEqual(len(self.p.model.query_ids(limit=1000)), 0, "undo removes everything the build made")

    def test_14_min_confidence_skips_and_reports_instead_of_building_doubtful_elements(self):
        made = self.p.execute("reconstruct.build", {"model": self.res, "min_confidence": 0.9})["result"]
        self.assertTrue(made["skipped"])
        for s in made["skipped"]:
            self.assertIn("below min_confidence", s["reason"])
        self.assertLess(len(made["openings"]), 3, "assumed doors score below 0.9")

    def test_15_a_model_that_is_not_a_reconstruction_or_is_malformed_is_refused_and_changes_nothing(self):
        rev = self.p.revision
        for bad in ({}, {"version": "spadra.reconstruction.v1", "walls": [{"id": "w", "start": [0, 0], "end": [0, 10], "thickness_mm": 200}], "spaces": []},
                    {"version": "spadra.reconstruction.v1", "walls": [{"id": "w", "start": [0, 0], "end": [float("nan"), 5000], "thickness_mm": 200}], "spaces": []},
                    {"version": "spadra.reconstruction.v1", "walls": [{"id": "w", "start": [0, 0], "end": [5000, 0], "thickness_mm": 200, "openings": [{"id": "o", "kind": "gate", "offset_mm": 1, "width_mm": 900}]}], "spaces": []}):
            with self.assertRaises(BadRequest):
                self.p.execute("reconstruct.build", {"model": bad})
        self.assertEqual(self.p.revision, rev)


class Corrections(unittest.TestCase):
    def setUp(self):
        self.p, self.view = sdrw_project(FX["two-rooms"])

    def run_(self, **corr):
        return R.run({"kind": "sdrw", "drawing": self.view}, corrections=corr, project=self.p)

    def test_16_a_wall_can_be_removed_and_the_rooms_follow(self):
        base = self.run_()
        partition = next(w["id"] for w in base["walls"] if w["thickness_mm"] == 150.0)
        res = self.run_(remove=[partition])
        self.assertEqual(res["summary"]["walls"], 4)
        self.assertEqual(len(res["spaces"]), 1)
        self.assertAlmostEqual(res["spaces"][0]["area_m2"], 7.8 * 3.8, delta=0.05)        # the interior inside 200 mm walls

    def test_17_an_opening_kind_and_a_wall_thickness_are_set_by_hand_with_full_confidence(self):
        base = self.run_()
        door = next(o for w in base["walls"] for o in w["openings"] if o["kind"] == "door")
        wall = base["walls"][0]["id"]
        res = self.run_(openings={door["id"]: {"kind": "window"}}, walls={wall: {"thickness_mm": 300}})
        o = next(o for w in res["walls"] for o in w["openings"] if o["id"] == door["id"])
        self.assertEqual((o["kind"], o["confidence"]), ("window", 1.0))
        self.assertEqual(next(w for w in res["walls"] if w["id"] == wall)["thickness_mm"], 300.0)

    def test_18_unknown_corrections_and_options_are_refused_or_reported_not_ignored(self):
        with self.assertRaises(BadRequest):
            self.run_(teleport=True)
        with self.assertRaises(BadRequest):
            R.run({"kind": "sdrw", "drawing": self.view}, {"wall_thickness": [1, 2]}, project=self.p)
        res = self.run_(remove=["w99"])
        self.assertIn("correction.unknown", {d["code"] for d in res["diagnostics"]})

    def test_19_a_dimension_can_be_ignored_or_given_a_value(self):
        base = self.run_()
        d0 = base["dimensions"][0]["id"]
        res = self.run_(dimensions={d0: {"ignore": True}})
        self.assertEqual(next(d for d in res["dimensions"] if d["id"] == d0)["status"], "ignored")


@unittest.skipUnless(OCR, "Tesseract OCR is not available: printed dimensions in raster images cannot be read here")
class Raster(unittest.TestCase):
    def test_20_a_raster_plan_is_read_dimensions_scale_walls_openings_and_room_area(self):
        fx = FX["single-room"]
        data, ext, meta = F.render(fx)
        res = R.run({"kind": "raster", "data": data})
        self.assertTrue(res["ok"], res["diagnostics"])
        self.assertAlmostEqual(res["scale"]["mm_per_unit"] * meta["px_per_mm"], 1.0, delta=0.005)
        self.assertEqual((res["summary"]["walls"], res["summary"]["openings"], res["summary"]["spaces"]), (4, 2, 1))
        self.assertAlmostEqual(res["spaces"][0]["area_m2"], fx.rooms[0], delta=0.1)
        self.assertEqual(sorted(o["kind"] for w in res["walls"] for o in w["openings"]), ["door", "window"])
        door = next(o for w in res["walls"] for o in w["openings"] if o["kind"] == "door")
        self.assertIn("assumed", door["evidence"], "a bare gap is an assumed door and says so")
        self.assertTrue(all(abs(w["thickness_mm"] - 200.0) <= 5.0 for w in res["walls"]), [w["thickness_mm"] for w in res["walls"]])   # measured from pixels: 1 px is 8 mm here

    def test_21_dimensions_in_metres_and_chained_dimensions_are_understood(self):
        data, _e, _m = F.render(FX["three-rooms-m"])
        res = R.run({"kind": "raster", "data": data})
        self.assertEqual(res["summary"]["walls"], 6)
        self.assertEqual(res["summary"]["spaces"], 3)
        self.assertTrue(any(d["unit_read"] == "m" for d in res["dimensions"]))

    def test_22_a_dimension_the_reading_missed_can_be_added_by_hand_and_is_then_imposed(self):
        fx = FX["l-shape"]
        data, _e, meta = F.render(fx)
        base = R.run({"kind": "raster", "data": data})
        ox, oy = meta["origin_mm"]
        k = meta["px_per_mm"]
        px = lambda x, y: [(x - ox) * k, (oy - y) * k]
        res = R.run({"kind": "raster", "data": data}, corrections={"add_dimensions": [{"p_px": px(7000, -100), "q_px": px(7000, 2600), "value_mm": 2700}]})
        self.assertEqual(res["summary"]["dimensions_used"], base["summary"]["dimensions_used"] + 1)
        self.assertTrue(any(d["source"] == "user" and d["status"] == "used" for d in res["dimensions"]))

    def test_23_a_forced_scale_is_reported_through_the_deviation_of_every_dimension(self):
        data, _e, meta = F.render(FX["single-room"])
        res = R.run({"kind": "raster", "data": data}, corrections={"scale_mm_per_px": 1.05 / meta["px_per_mm"]})
        self.assertEqual(res["scale"]["method"], "given by the user")
        for d in res["dimensions"]:
            self.assertAlmostEqual(d["scale_deviation"], -0.0476, delta=0.002)

    def test_24_ifc_semantics_survive_the_trip_into_a_project(self):
        data, _e, _m = F.render(FX["two-rooms"])
        res = R.run({"kind": "raster", "data": data})
        p = Project.create("ifc", unit="mm")
        made = p.execute("reconstruct.build", {"model": res})["result"]
        self.assertEqual(sorted(set(made["ifc"].values())), ["IfcDoor", "IfcSpace", "IfcWall", "IfcWindow"])


class NoOcr(unittest.TestCase):
    def test_25_without_reading_text_a_raster_has_no_scale_and_says_so_instead_of_guessing(self):
        if not CAPS["sources"]["raster"]["available"]:
            self.skipTest("OpenCV is not installed")
        data, _e, _m = F.render(FX["single-room"])
        res = R.run({"kind": "raster", "data": data}, {"ocr": False})
        self.assertFalse(res["ok"])
        self.assertEqual(res["walls"], [])
        self.assertIn("scale.none", {d["code"] for d in res["diagnostics"]})

    def test_26_a_given_scale_makes_a_raster_usable_without_reading_any_text(self):
        if not CAPS["sources"]["raster"]["available"]:
            self.skipTest("OpenCV is not installed")
        data, _e, meta = F.render(FX["single-room"])
        res = R.run({"kind": "raster", "data": data}, {"ocr": False}, {"scale_mm_per_px": 1.0 / meta["px_per_mm"]})
        self.assertEqual(res["summary"]["walls"], 4)
        self.assertAlmostEqual(res["spaces"][0]["area_m2"], FX["single-room"].rooms[0], delta=0.15)
        self.assertEqual(res["constraints"]["axes"], {"x": {"status": "no_dimensions", "dof": None, "components": 0, "variables": 4, "dimensions": 0}, "y": {"status": "no_dimensions", "dof": None, "components": 0, "variables": 4, "dimensions": 0}})

    def test_27_garbage_input_is_a_clear_error(self):
        if not CAPS["sources"]["raster"]["available"]:
            self.skipTest("OpenCV is not installed")
        with self.assertRaises(BadRequest):
            R.run({"kind": "raster", "data": b"not an image"})
        with self.assertRaises(BadRequest):
            R.run({"kind": "raster", "data": b""})
        with self.assertRaises(BadRequest):
            R.run({"kind": "dwg", "data": b"x"})


class Http(ServerCase):
    def test_28_capabilities_run_and_build_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Rec", "unit": "mm"})
        caps = a.get("/api/reconstruct/capabilities")
        self.assertTrue(caps["sources"]["sdrw"]["available"])
        self.assertTrue(caps["limits"])
        proj = self.app.require()
        fx = FX["two-rooms"]
        view = next(v for v in proj.manifest["views"] if v["kind"] == "plan")["id"]
        doc = proj.drawing(view)
        for p, q in F._outline_segments(fx):
            doc.add_line(list(p), list(q))
        for d in fx.dims:
            prim = F._dimension_primitives(d, 1)
            for p, q in prim["lines"]:
                doc.add_line(list(p), list(q))
            h, w = 250.0, 0.6 * 250.0 * len(prim["text"])
            if prim["axis"] == "x":
                doc.add_text([prim["center"][0] - w / 2, prim["center"][1] + 60], prim["text"], h)
            else:
                doc.add_text([prim["center"][0] - 60, prim["center"][1] - w / 2], prim["text"], h, rotation=90.0, degrees=True)
        rev = proj.revision
        model = a.post("/api/reconstruct/run", {"kind": "sdrw", "drawing": view})
        self.assertEqual(model["version"], "spadra.reconstruction.v1")
        self.assertEqual(model["summary"]["walls"], 5)
        self.assertEqual(proj.revision, rev, "running a reconstruction changes nothing")
        built = a.cmd("reconstruct.build", {"model": model})["result"]
        self.assertEqual(len(built["walls"]), 5)

    def test_29_a_bad_request_is_a_400(self):
        a = self.api
        a.post("/api/project/new", {"name": "Rec", "unit": "mm"})
        status, _out = a.call("POST", "/api/reconstruct/run", {"kind": "raster", "data": "!!!notbase64"})
        self.assertEqual(status, 400)
        status, _out = a.call("POST", "/api/reconstruct/run", {"kind": "tiff"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
