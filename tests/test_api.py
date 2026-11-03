"""SPADRA's HTTP API end to end: real server, real SLAI SGEO/SDRW documents, real files."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import unittest

from applications.SPADRA.animation_bridge import BRIDGE
from applications.SPADRA.engineering_bridge import BRIDGE as ENGINEERING

from .harness import Api, ServerCase


class Access(ServerCase):
    def test_no_cookie_no_data_and_writes_need_the_marker(self):
        anon = Api(self.server.port, signed_in=False)
        self.assertEqual(anon.call("GET", "/api/bootstrap")[0], 401)
        self.assertEqual(self.api.call("POST", "/api/project/new", {"name": "X"}, marker=False)[0], 401)
        self.assertEqual(self.api.call("POST", "/api/project/new", {"name": "X"}, origin="http://evil.example")[0], 401)
        self.assertEqual(self.api.call("POST", "/api/project/new", {"name": "X"})[0], 201)

    def test_bootstrap_reports_only_what_is_implemented(self):
        caps = self.api.get("/api/bootstrap")["capabilities"]
        self.assertTrue(caps["drawing2d"] and caps["model3d"] and caps["exportSvg"])
        self.assertTrue(caps["sections"] and caps["elevations"] and caps["schedules"])
        self.assertEqual(caps["animation"], BRIDGE.available)          # true only where the real SLAI Animation Agent can be loaded
        self.assertEqual(caps["engineering"], ENGINEERING.available)   # true only where the real SLAI STEM Agent can be loaded
        self.assertTrue(caps["assistant"])                                  # the assistant works without any agent: it answers from the model and says what it cannot do
        from applications.SPADRA.optimization_bridge import status as optimization_status
        from applications.SPADRA.simulation_bridge import status as simulation_status
        from applications.SPADRA.spatial_bridge import BRIDGE as SPATIAL
        self.assertEqual(caps["simulation"], simulation_status()["available"])        # true only where the real Simulation and STEM agents load
        self.assertEqual(caps["optimization"], optimization_status()["available"])
        self.assertEqual(caps["spatial"], SPATIAL.status()["available"])
        self.assertEqual(caps["calculator"], ENGINEERING.available)

    def test_commands_need_an_open_project(self):
        status, out = self.api.call("GET", "/api/project/tree")
        self.assertEqual((status, out["error"]["code"]), (400, "bad_request"))


class Lifecycle(ServerCase):
    def start(self):
        self.api.post("/api/project/new", {"name": "Community Pavilion", "unit": "mm"})
        tree = self.api.get("/api/project/tree")
        self.plan = tree["tree"]["sections"][0]["children"][1]["children"][0]["id"]
        self.level = tree["levels"][0]["id"]

    def test_full_workflow_over_http_then_close_and_reopen(self):
        self.start()
        a = self.api
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [8400, 0], "level": self.level, "thickness": 250, "height": 3.2})["result"]["id"]
        line = a.cmd("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [6400, 0], "layer": "A-WALL"})["result"]["id"]
        a.cmd("draw.dimension", {"drawing": self.plan, "p1": [0, 0], "p2": [6400, 0], "dimline_point": [0, -600]})
        a.cmd("sheet.create", {"name": "Level 01 Plan & Section"})
        props = a.post("/api/properties", {"model": wall})
        self.assertEqual((props["title"], props["category"]), ("Basic Wall", "wall"))
        self.assertEqual(a.post("/api/properties", {"drawing": self.plan, "entity": line})["category"], "line")
        a.cmd("model.set_property", {"id": wall, "key": "height", "value": 3.5})
        scene = a.get("/api/scene")
        zs = [v[2] for v in scene["objects"][0]["vertices"]]
        self.assertAlmostEqual(max(zs) - min(zs), 3.5, places=3)
        self.assertEqual(len(a.get(f"/api/drawing/{self.plan}")["entities"]), 2)
        saved = a.post("/api/project/save", {"name": "Community Pavilion"})
        self.assertFalse(saved["project"]["dirty"])
        self.assertTrue(saved["path"].endswith(".spadra"))
        a.post("/api/project/close")
        self.assertEqual(a.call("GET", "/api/scene")[0], 400)
        reopened = a.post("/api/project/open", {"path": saved["path"]})
        self.assertEqual(reopened["name"], "Community Pavilion")
        scene2 = a.get("/api/scene")
        self.assertEqual([o["id"] for o in scene2["objects"]], [wall])
        self.assertEqual(scene2["objects"][0]["vertices"], scene["objects"][0]["vertices"])
        self.assertEqual(len(a.get(f"/api/drawing/{self.plan}")["entities"]), 2)
        self.assertEqual(self.api.get("/api/bootstrap")["recent"][0]["name"], "Community Pavilion")

    def test_undo_redo_and_stale_revisions(self):
        self.start()
        rev = self.api.cmd("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [10, 0]})["revision"]
        status, out = self.api.call("POST", "/api/command", {"op": "draw.line", "expected_revision": rev - 1,
                                                              "params": {"drawing": self.plan, "start": [1, 1], "end": [2, 2]}})
        self.assertEqual((status, out["error"]["code"]), (409, "conflict"))
        self.assertEqual(len(self.api.get(f"/api/drawing/{self.plan}")["entities"]), 1)
        self.api.post("/api/undo")
        self.assertEqual(self.api.get(f"/api/drawing/{self.plan}")["entities"], [])
        self.api.post("/api/redo")
        self.assertEqual(len(self.api.get(f"/api/drawing/{self.plan}")["entities"]), 1)
        self.assertEqual(self.api.call("POST", "/api/redo", {})[0], 400)

    def test_errors_use_the_envelope_and_change_nothing(self):
        self.start()
        for body in ({"op": "draw.line", "params": {"drawing": self.plan, "start": [0, 0], "end": [0, 0]}}, {"op": "no.such", "params": {}},
                     {"op": "draw.line", "params": {"drawing": "missing", "start": [0, 0], "end": [1, 1]}}, {"op": "draw.line", "params": []}):
            status, out = self.api.call("POST", "/api/command", body)
            self.assertIn(status, (400, 404), body)
            self.assertFalse(out["ok"])
            self.assertTrue(out["error"]["message"])
        self.assertEqual(self.api.get("/api/project/tree")["project"]["revision"], 0)

    def test_unsaved_work_is_protected(self):
        self.start()
        self.api.cmd("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [10, 0]})
        self.assertEqual(self.api.call("POST", "/api/project/new", {"name": "Other"})[0], 409)
        self.assertEqual(self.api.call("POST", "/api/project/close", {})[0], 409)
        self.assertEqual(self.api.call("POST", "/api/project/new", {"name": "Other", "discard": True})[0], 201)     # an explicit choice to discard
        self.assertEqual(self.api.get("/api/bootstrap")["project"]["name"], "Other")
        self.api.cmd("draw.line", {"drawing": self.api.get("/api/project/tree")["tree"]["sections"][0]["children"][1]["children"][0]["id"], "start": [0, 0], "end": [5, 5]})
        self.assertEqual(self.api.call("POST", "/api/project/close", {"discard": True})[0], 200)

    def test_save_as_over_http_continues_on_an_independent_copy(self):
        a = self.api
        a.post("/api/project/new", {"name": "Hall", "unit": "mm"})
        first = a.post("/api/project/save", {"name": "Hall"})
        copy = a.post("/api/project/save", {"name": "Hall v2", "save_as": True})
        self.assertNotEqual(copy["path"], first["path"])
        self.assertEqual((copy["project"]["name"], copy["project"]["dirty"]), ("Hall v2", False))
        self.assertEqual(a.post("/api/project/save", {})["path"], copy["path"])           # plain Save now writes the copy
        self.assertEqual(a.call("POST", "/api/project/save", {"save_as": True})[0], 400)      # a copy needs a name
        self.assertEqual(a.post("/api/project/open", {"path": first["path"], "discard": True})["name"], "Hall")

    def test_properties_backups_and_recovery_over_http(self):
        a = self.api
        self.start()
        unsaved = a.get("/api/project/properties")
        self.assertEqual((unsaved["path"], unsaved["backups"]), (None, []))
        self.assertEqual(a.call("POST", "/api/project/recover", {"backup": "20260101T000000Z-r1", "discard": True})[0], 400)       # nothing saved, so no backups
        a.cmd("draw.line", {"drawing": self.plan, "start": [0, 0], "end": [100, 0]})
        first = a.post("/api/project/save", {"name": "Backed"})
        self.assertIsNone(first["backup"])
        a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level, "thickness": 200, "height": 3})
        second = a.post("/api/project/save", {})
        self.assertIsNotNone(second["backup"])
        props = a.get("/api/project/properties")
        self.assertEqual([b["id"] for b in props["backups"]], [second["backup"]])
        self.assertEqual(props["counts"]["elements"]["wall"], 1)
        self.assertEqual((props["name"], props["dirty"], props["path"]), ("Community Pavilion", False, second["path"]))
        a.cmd("project.set", {"name": "Renamed", "description": "Notes"})
        self.assertEqual(a.call("POST", "/api/project/recover", {"backup": second["backup"]})[0], 409)            # unsaved changes are not discarded silently
        rec = a.post("/api/project/recover", {"backup": second["backup"], "discard": True})
        self.assertEqual((rec["name"], rec["dirty"], rec["path"]), ("Community Pavilion (recovered)", True, None))
        self.assertEqual(a.get("/api/project/properties")["counts"]["elements"].get("wall", 0), 0, "the wall was added after that backup")
        for bad in ("../x", "", "20260101T000000Z-r1/..", None):
            self.assertEqual(a.call("POST", "/api/project/recover", {"backup": bad, "discard": True})[0], 400, repr(bad))
        self.assertEqual(a.call("POST", "/api/command", {"op": "project.set", "params": {}})[0], 400)
        self.assertEqual(a.call("POST", "/api/command", {"op": "project.set", "params": {"name": ""}})[0], 400)

    def test_save_paths_are_validated(self):
        self.start()
        self.assertEqual(self.api.call("POST", "/api/project/save", {"path": str(self.dir / "bad-name")})[0], 400)
        first = self.api.post("/api/project/save", {"name": "Dup"})["path"]
        self.api.post("/api/project/new", {"name": "Dup"})
        second = self.api.post("/api/project/save", {"name": "Dup"})["path"]
        self.assertNotEqual(first, second)                                      # never overwrites the first project
        self.assertEqual(self.api.call("POST", "/api/project/open", {"path": str(self.dir / "ghost.spadra")})[0], 404)


class Exports(ServerCase):
    def test_svg_and_glb_are_real_files(self):
        self.api.post("/api/project/new", {"name": "Exp"})
        tree = self.api.get("/api/project/tree")
        plan, level = tree["tree"]["sections"][0]["children"][1]["children"][0]["id"], tree["levels"][0]["id"]
        self.assertEqual(self.api.call("GET", "/api/export/model")[0], 404)                 # empty model: nothing to export, and it says so
        self.api.cmd("draw.rectangle", {"drawing": plan, "corner": [0, 0], "width": 6400, "height": 4200})
        self.api.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level})
        status, headers, body = self.api.raw("GET", f"/api/export/drawing/{plan}")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        root = ET.fromstring(body)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertGreaterEqual(len(list(root.iter())), 2)
        status, headers, glb = self.api.raw("GET", "/api/export/model?format=glb")
        self.assertEqual((status, glb[:4]), (200, b"glTF"))
        status, _, stl = self.api.raw("GET", "/api/export/model?format=stl")
        self.assertEqual(status, 200)
        self.assertGreater(len(stl), 100)
        self.assertIn("report", self.api.get(f"/api/export/drawing/{plan}?report=1"))
        status, _, obj = self.api.raw("GET", "/api/export/model?format=obj")
        self.assertEqual(status, 200)
        self.assertTrue(obj.startswith(b"# SLAI SGEO export"))
        self.assertEqual(self.api.call("GET", "/api/export/model?format=step")[0], 400)               # investigated, and not offered: the adapter cannot convert a building model

class Reports(ServerCase):
    def test_a_report_is_made_read_and_downloaded_as_sldc(self):
        from src.utils import sldc
        a = self.api
        a.post("/api/project/new", {"name": "R", "unit": "mm"})
        rid = a.cmd("report.create", {"name": "Status"})["result"]["id"]
        view = a.get(f"/api/report/{rid}")
        self.assertIn("# Status", view["markdown"])
        self.assertEqual(view["container"]["version"], "1.0.0")
        status, headers, raw = a.raw("GET", f"/api/export/report/{rid}")
        self.assertEqual(status, 200)
        self.assertEqual(sldc.loads_package(raw).document["title"], "Status")
        self.assertEqual(a.call("GET", "/api/report/nope")[0], 404)


class Interchange(ServerCase):
    def test_inspect_then_import_native_and_obj_over_http(self):
        import base64
        from applications.SPADRA import interop as IO
        a = self.api
        a.post("/api/project/new", {"name": "A", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level})
        status, _, sgeo = a.raw("GET", "/api/export/model?format=sgeo")
        self.assertEqual(status, 200)
        report = a.get("/api/export/model?format=sgeo&report=1")["report"]
        self.assertEqual((report["status"], report["destination"]["format"]), ("complete", "sgeo"))
        a.post("/api/project/new", {"name": "B", "unit": "mm", "discard": True})
        info = a.post("/api/import/inspect", {"target": "model", "format": "sgeo", "data": base64.b64encode(sgeo).decode(), "name": "a.sgeo"})
        self.assertTrue(info["valid"])
        self.assertEqual(info["counts"]["objects"], 1)
        bad = a.post("/api/import/inspect", {"target": "model", "format": "glb", "data": base64.b64encode(sgeo).decode()})
        self.assertFalse(bad["valid"])
        self.assertEqual(bad["detected"], "sgeo")
        done = a.cmd("model.import", {"format": "sgeo", "data": base64.b64encode(sgeo).decode(), "name": "a.sgeo"})["result"]
        self.assertEqual((done["objects"], done["report"]["status"]), (1, "complete"))
        self.assertEqual(a.call("POST", "/api/import/inspect", {"target": "sheet", "format": "glb", "data": "AAAA"})[0], 400)
        self.assertTrue(IO.availability()["importModel"]["obj"]["available"])
        self.assertEqual(a.get("/api/interop")["importModel"]["sgeo"]["native"], True)


class Documentation(ServerCase):
    def test_sections_elevations_and_schedules_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Docs", "unit": "mm"})
        tree = a.get("/api/project/tree")
        level = tree["levels"][0]["id"]
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [6000, 0], "level": level})["result"]["id"]
        a.cmd("model.add_door", {"wall": wall, "point": [1500, 0], "width": 900, "height": 2100})
        section = a.cmd("view.add_section", {"a": [3000, -2000], "b": [3000, 2000]})["result"]
        data = a.get(f"/api/drawing/{section['id']}")
        self.assertEqual(data["projection"]["kind"], "section")
        self.assertEqual(len([i for i in data["projection"]["items"] if i["cut"]]), 1)
        south = next(v for v in a.get("/api/project/tree")["tree"]["sections"][0]["children"][3]["children"] if v["label"] == "South")
        self.assertEqual(a.get(f"/api/drawing/{south['id']}")["projection"]["kind"], "elevation")
        sched = a.cmd("view.add_schedule", {"category": "door"})["result"]
        rows = a.get(f"/api/schedule/{sched['id']}")
        self.assertEqual((rows["count"], rows["rows"][0]["width"], rows["title"]), (1, 900.0, "Door Schedule"))
        status, headers, raw = a.raw("GET", f"/api/export/schedule/{sched['id']}")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type", headers.get("content-type", "")))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbfNo.,Name"))
        self.assertEqual(a.call("GET", f"/api/schedule/{section['id']}")[0], 404)
        self.assertEqual(a.call("GET", "/api/schedule/nope")[0], 404)



class Animation(ServerCase):
    @unittest.skipUnless(BRIDGE.available, "the SLAI Animation Agent is not importable here")
    def test_animation_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Anim", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        self.assertIsNone(a.get("/api/animation")["animation"])
        spec = {"duration": 4.0, "tracks": [{"object_id": wall, "property": "translation", "interpolation": "linear",
                                              "keyframes": [{"time": 0, "value": [0, 0, 0]}, {"time": 4, "value": [0, 0, 2]}]}]}
        out = a.cmd("anim.edit", {"operation": "set_animation", "payload": {"animation": spec}})
        self.assertEqual(out["result"]["operation"], "set_animation")
        data = a.get("/api/animation")
        self.assertEqual(data["animation"], spec)
        self.assertEqual(data["names"], {wall: "Basic Wall"})
        self.assertTrue(data["agent"]["available"])
        z = lambda t: min(v[2] for v in a.get(f"/api/scene?time={t}")["objects"][0]["vertices"])
        self.assertAlmostEqual(z(2) - z(0), 1.0, places=9)
        self.assertEqual(a.get("/api/scene?time=2")["time"], 2.0)
        self.assertEqual(a.call("GET", "/api/scene?time=abc")[0], 400)
        self.assertEqual(a.call("GET", "/api/scene?time=-1")[0], 400)
        status, body = a.call("POST", "/api/command", {"op": "anim.edit", "params": {"operation": "add_keyframe", "payload": {"track_index": 5, "keyframe": {"time": 1, "value": [0, 0, 0]}}}})
        self.assertEqual(status, 400)
        self.assertEqual(a.get("/api/animation")["animation"], spec)
        a.post("/api/undo", {})
        self.assertIsNone(a.get("/api/animation")["animation"])

    @unittest.skipUnless(BRIDGE.available, "the SLAI Animation Agent is not importable here")
    def test_authoring_and_pose_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Author", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        first = a.get("/api/animation")
        self.assertEqual((first["lanes"], first["markers"], first["settings"]), ([], [], {"fps": 24, "loop": False, "duration": 5.0}))
        self.assertEqual(first["properties"], ["translation", "turn", "scale", "visibility"])
        a.cmd("anim.key", {"id": wall, "property": "translation", "time": 5, "value": [0, 0, 2]})
        a.cmd("anim.pose", {"id": wall, "time": 5, "values": {"turn": 90, "scale": [2, 1, 1]}})
        a.cmd("anim.marker", {"time": 2, "name": "Half way"})
        data = a.get("/api/animation")
        self.assertEqual({(l["property"], l["interpolation"]) for l in data["lanes"]}, {("translation", "smooth"), ("turn", "smooth"), ("scale", "smooth")})
        self.assertEqual(data["markers"][0]["name"], "Half way")
        pose = a.get(f"/api/animation/pose?id={wall}&time=5")
        self.assertAlmostEqual(pose["pose"]["translation"][2], 2.0, places=9)
        self.assertAlmostEqual(pose["pose"]["turn"], 90.0, places=6)
        self.assertEqual(sorted(pose["keyed"]), ["scale", "translation", "turn"])
        self.assertEqual(a.get(f"/api/animation/pose?id={wall}&time=0")["pose"]["translation"], [0.0, 0.0, 0.0])
        self.assertEqual(a.call("GET", "/api/animation/pose?id=nope&time=0")[0], 404)
        self.assertEqual(a.call("GET", f"/api/animation/pose?id={wall}&time=x")[0], 400)
        self.assertEqual(a.call("POST", "/api/command", {"op": "anim.key", "params": {"id": wall, "property": "colour", "time": 1, "value": 1}})[0], 400)
        self.assertEqual(a.call("POST", "/api/command", {"op": "anim.settings", "params": {"fps": 500}})[0], 400)
        saved = a.post("/api/project/save", {"name": "Author"})
        self.assertFalse(saved["project"]["dirty"])


class Engineering(ServerCase):
    @unittest.skipUnless(ENGINEERING.available, "the SLAI STEM Agent is not importable here")
    def test_beam_analysis_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Beams", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        beam = a.cmd("model.add_beam", {"start": [0, 0], "end": [6000, 0], "level": level, "top": 3, "width": 200, "depth": 400, "material": "Steel"})["result"]["id"]
        timber = a.cmd("model.add_beam", {"start": [0, 1000], "end": [6000, 1000], "level": level, "top": 3, "width": 200, "depth": 400, "material": "Timber"})["result"]["id"]
        info = a.get("/api/engineering")
        self.assertTrue(info["available"])
        self.assertIn("cantilever", info["supports"])
        revision = a.get("/api/bootstrap")["project"]["revision"]
        out = a.post("/api/engineering/beam", {"id": beam, "support": "simply_supported", "load_type": "udl", "load": 10})
        self.assertAlmostEqual(out["outputs"]["max_moment_nm"]["value"], 45_000.0, places=6)           # w L^2 / 8
        self.assertAlmostEqual(out["outputs"]["max_shear_n"]["value"], 30_000.0, places=6)             # w L / 2
        self.assertEqual(out["agent"], "SLAI STEM Agent")
        self.assertEqual(a.get("/api/bootstrap")["project"]["revision"], revision)                     # reading changes nothing
        status, body = a.call("POST", "/api/engineering/beam", {"id": timber, "load": 10})
        self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))                        # no modulus is invented
        self.assertEqual(a.post("/api/engineering/beam", {"id": timber, "load": 10, "youngs_modulus_gpa": 11})["inputs"]["youngs_modulus_gpa"], 11.0)
        self.assertEqual(a.call("POST", "/api/engineering/beam", {"id": beam, "load": 10, "support": "cantilever", "load_type": "point_center"})[0], 400)
        self.assertEqual(a.call("POST", "/api/engineering/beam", {"id": "nope", "load": 10})[0], 404)


class Interop(ServerCase):
    def test_conversions_over_http(self):
        import base64
        a = self.api
        a.post("/api/project/new", {"name": "IO", "unit": "mm"})
        caps = a.get("/api/interop")
        self.assertTrue(caps["importModel"]["glb"]["available"] and caps["exportDrawing"]["svg"]["available"])
        level = a.get("/api/project/tree")["levels"][0]["id"]
        a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level})
        status, headers, glb = a.raw("GET", "/api/export/model?format=glb")
        self.assertEqual((status, glb[:4]), (200, b"glTF"))
        out = a.cmd("model.import", {"format": "glb", "data": base64.b64encode(glb).decode(), "name": "wall.glb"})["result"]
        self.assertEqual(out["objects"], 1)
        self.assertEqual(out["report"]["adapter"], "glb-native")
        self.assertEqual(len(a.get("/api/scene")["objects"]), 2)
        self.assertEqual(a.call("POST", "/api/command", {"op": "model.import", "params": {"format": "glb", "data": base64.b64encode(b"nope").decode()}})[0], 400)
        plan = a.get("/api/project/tree")["tree"]["sections"][0]["children"][1]["children"][0]["id"]
        a.cmd("draw.line", {"drawing": plan, "start": [0, 0], "end": [1000, 0]})
        status, headers, svg = a.raw("GET", f"/api/export/drawing/{plan}")
        self.assertEqual(status, 200)
        report = a.get(f"/api/export/drawing/{plan}?report=1")["report"]
        self.assertIn("accuracy_class", report)
        status, body = a.call("GET", f"/api/export/drawing/{plan}?format=dxf")
        if caps["exportDrawing"]["dxf"]["available"]:
            self.assertEqual(status, 200)
        else:
            self.assertIn(status, (400, 424, 501, 503))
            self.assertIn("ezdxf", body["error"]["message"])
        self.assertEqual(a.call("GET", f"/api/export/drawing/{plan}?format=dwg")[0], 400)


class Assistant(ServerCase):
    """The assistant, agent list, calculator and engineering routes over HTTP (a stale approval is a 409, an unsupported request is a message, not an error)."""

    def test_assistant_flow_over_http(self):
        a = self.api
        self.assertEqual(a.call("GET", "/api/assistant")[0], 400)                   # needs a project
        a.post("/api/project/new", {"name": "Chat", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [5000, 0], "level": level})["result"]["id"]
        m = a.post("/api/assistant/ask", {"request": "Change the selected wall height to 3.4 meters", "context": {"selection": [wall]}})["message"]
        self.assertEqual(m["kind"], "proposal")
        pr = m["proposal"]
        self.assertEqual(a.post("/api/properties", {"model": wall})["top"][2]["value"], 3.0)                 # asking changed nothing
        a.cmd("model.set_property", {"id": wall, "key": "thickness", "value": 250})                          # the project moves on
        status, body = a.call("POST", "/api/assistant/approve", {"proposal": pr["id"], "expected_revision": pr["revision"]})
        self.assertEqual((status, body["error"]["code"]), (409, "conflict"))
        m2 = a.post("/api/assistant/ask", {"request": "Change the selected wall height to 3.4 meters", "context": {"selection": [wall]}})["message"]
        done = a.post("/api/assistant/approve", {"proposal": m2["proposal"]["id"], "expected_revision": m2["proposal"]["revision"]})["message"]
        self.assertEqual(done["kind"], "success")
        self.assertEqual(a.post("/api/properties", {"model": wall})["top"][2]["value"], 3.4)
        history = a.get("/api/assistant")
        self.assertFalse(history["interpreter"]["language_model"])
        self.assertGreaterEqual(len(history["messages"]), 3)
        self.assertEqual(a.call("POST", "/api/assistant/ask", {"request": "x" * 700})[0], 400)         # an over-long request is refused
        self.assertEqual(a.post("/api/assistant/ask", {"request": "write me a poem"})["message"]["kind"], "unsupported")

    def test_agent_list_is_honest(self):
        rows = self.api.get("/api/agents")["agents"]
        by = {r["key"]: r for r in rows}
        self.assertTrue(all(not by[k]["available"] for k in by if not by[k]["integrated"]))
        self.assertTrue(by["language"]["why_not"])

    def test_calculator_routes(self):
        a = self.api
        if not ENGINEERING.available:
            self.assertEqual(a.call("POST", "/api/calculator/convert", {"value": 1, "from": "m", "to": "mm"})[0], 503)
            return
        self.assertEqual(a.post("/api/calculator/convert", {"value": 1, "from": "m", "to": "mm"})["result"]["value"], 1000.0)
        self.assertEqual(a.call("POST", "/api/calculator/convert", {"value": 1, "from": "m", "to": "kg"})[0], 400)
