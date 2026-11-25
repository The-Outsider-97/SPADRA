"""Profiler hand-off into a real SPADRA project: volumes, links, resync, refusals. Asserts on the geometry the model really holds."""
from __future__ import annotations

import math
import unittest

from . import profiler_env  # noqa: F401
from .harness import ServerCase
from .. import elements as E
from ..profiler.transfer import LINK_KEY


class Transfer(ServerCase):
    def setUp(self):
        super().setUp()
        self.api.post("/api/project/new", {"name": "P", "unit": "mm"})
        self.project = self.app.require()

    def new(self, template="blank", **kw):
        return self.api.post("/api/profiler/new", {"template": template, **kw})["status"]["id"]

    def op(self, sid, op, **args):
        return self.api.post("/api/profiler/%s/op" % sid, {"op": op, "args": args})

    def send(self, sid, kind, **options):
        return self.api.post("/api/profiler/%s/send" % sid, {"kind": kind, "options": options})

    def volume(self, oid):
        st = E.solid_status(self.project, oid)
        self.assertEqual(st.state, "solid", st.message)
        return st.volume

    def test_extrusion_volume_and_embedded_link(self):
        sid = self.new("rectangle")                                                # 120 x 100 mm
        res = self.send(sid, "extrude", depth=0.3, mode="embedded")["result"]
        self.assertAlmostEqual(self.volume(res["id"]), 0.120 * 0.100 * 0.3, 9)
        link = E.m(E.meta_of(self.project, res["id"]), LINK_KEY)
        self.assertEqual(link["mode"], "embedded")
        self.assertEqual(link["params"]["profiler.Width"], 120)
        self.assertIn("embedded", link)

    def test_hole_becomes_a_companion_void_of_the_right_size(self):
        sid = self.new()
        self.op(sid, "rectangle", p1=[0, 0], p2=[1000, 600])
        self.op(sid, "circle", center=[500, 300], radius=100)
        res = self.send(sid, "extrude", depth=0.3)["result"]
        solid, hole = res["ids"]
        exact = (1.0 * 0.6 - math.pi * 0.1 ** 2) * 0.3
        self.assertLess(abs(self.volume(solid) - exact), 1e-5)                     # the circle really is cut out (the model holds it as a many-sided polygon)
        meta = E.meta_of(self.project, hole)
        self.assertEqual(E.category(self.project, hole), "void")

    def test_void_and_opening_refusals_leave_the_model_alone(self):
        sid = self.new("rectangle")
        before = self.project.revision
        status, body = self.api.call("POST", "/api/profiler/%s/send" % sid, {"kind": "opening", "options": {}})
        self.assertEqual(status, 400)                                              # an opening needs the door or window it belongs to
        self.assertEqual(self.project.revision, before)

    def test_revolve_volume_matches_pappus(self):
        sid = self.new()
        self.op(sid, "rectangle", p1=[10, 0], p2=[20, 50])                         # a tube: radius 10..20 mm, 50 mm high, axis = the vertical axis
        res = self.send(sid, "revolve", axis={"point": [0, 0], "angle": 90}, angle=360, segments=96)["result"]
        exact = math.pi * (20 ** 2 - 10 ** 2) * 50 * 1e-9                          # m^3
        got = self.volume(res["id"])
        self.assertLess(abs(got - exact) / exact, 0.002)                           # a 96-sided approximation of a circle is within 0.2 %

    def tube_with_cavity(self):
        sid = self.new()
        self.op(sid, "rectangle", p1=[10, 0], p2=[30, 40])                         # section 20 x 40, radii 10..30 mm
        self.op(sid, "rectangle", p1=[15, 10], p2=[25, 30])                        # a 10 x 20 hole in it
        return sid

    def test_revolve_with_an_inner_loop_makes_a_cavity(self):
        sid = self.tube_with_cavity()
        res = self.send(sid, "revolve", axis={"point": [0, 0], "angle": 90}, angle=360, segments=180)["result"]
        exact = 2 * math.pi * (20 * 40 * 20 - 10 * 20 * 20) * 1e-9                 # Pappus: 2 pi x (area x centroid radius), the hole taken out
        self.assertLess(abs(self.volume(res["id"]) - exact) / exact, 0.001)

    def test_partial_revolve_with_an_inner_loop_has_capped_ends(self):
        sid = self.tube_with_cavity()
        res = self.send(sid, "revolve", axis={"point": [0, 0], "angle": 90}, angle=180, segments=90)["result"]
        exact = math.pi * (20 * 40 * 20 - 10 * 20 * 20) * 1e-9
        self.assertLess(abs(self.volume(res["id"]) - exact) / exact, 0.001)

    def test_revolve_resync_follows_the_profile_when_the_hole_is_removed(self):
        sid = self.tube_with_cavity()
        opts = {"axis": {"point": [0, 0], "angle": 90}, "angle": 360, "segments": 120}
        oid = self.send(sid, "revolve", **opts)["result"]["id"]
        with_hole = self.volume(oid)
        edges = self.api.get("/api/profiler/%s" % sid)["render"]["edges"]
        self.op(sid, "delete", ids=[e["id"] for e in edges[4:]])                    # the four lines of the hole
        self.api.post("/api/profiler/resync", {"id": oid, "sid": sid, "options": opts})
        solid = 2 * math.pi * (20 * 40 * 20) * 1e-9
        self.assertGreater(self.volume(oid), with_hole * 1.2)
        self.assertLess(abs(self.volume(oid) - solid) / solid, 0.001)

    def test_revolve_across_the_axis_is_refused(self):
        sid = self.new()
        self.op(sid, "rectangle", p1=[-5, 0], p2=[5, 10])
        before = self.project.revision
        status, _ = self.api.call("POST", "/api/profiler/%s/send" % sid, {"kind": "revolve", "options": {"axis": {"point": [0, 0], "angle": 90}}})
        self.assertEqual(status, 400)
        self.assertEqual(self.project.revision, before)

    def test_sweep_of_a_section_along_a_path(self):
        sid = self.new()
        self.op(sid, "rectangle", p1=[0, 0], p2=[100, 50])                         # 100 x 50 mm section
        res = self.send(sid, "sweep", role="section", path_points=[[0, 0], [2000, 0]])["result"]
        self.assertAlmostEqual(self.volume(res["id"]), 0.100 * 0.050 * 2.0, 6)

    def test_resync_updates_the_same_object_from_new_parameter_values(self):
        sid = self.new("rectangle")
        oid = self.send(sid, "extrude", depth=0.5)["result"]["id"]
        self.assertAlmostEqual(self.volume(oid), 0.120 * 0.100 * 0.5, 9)
        self.api.post("/api/profiler/resync", {"id": oid, "sid": sid, "overrides": {"Width": 200}})
        self.assertAlmostEqual(self.volume(oid), 0.200 * 0.100 * 0.5, 9)           # same id, new size
        info = self.api.get("/api/profiler-link?id=%s" % oid)
        self.assertTrue(info["linked"])
        self.assertEqual(info["overrides"], {"Width": 200})
        self.assertEqual(self.api.get("/api/profiler-link?id=%s" % self.project.model.add_box(width=1, depth=1, height=1, name="x"))["linked"], False)

    def test_linked_mode_needs_a_saved_clean_profile(self):
        sid = self.new("rectangle")
        status, body = self.api.call("POST", "/api/profiler/%s/send" % sid, {"kind": "extrude", "options": {"mode": "linked"}})
        self.assertEqual(status, 400)
        path = str(self.dir / "p.sdrw")
        self.api.post("/api/profiler/%s/save" % sid, {"path": path})
        oid = self.send(sid, "extrude", mode="linked", depth=0.2)["result"]["id"]
        link = E.m(E.meta_of(self.project, oid), LINK_KEY)
        self.assertEqual(link["mode"], "linked")
        self.assertTrue(link["file"].endswith("p.sdrw"))
        self.assertIn("file_sha256", link)

    def test_opening_profile_for_a_door(self):
        l1 = self.project.manifest["levels"][0]["id"]
        wall = self.project.execute("model.add_wall", {"start": [0, 0], "end": [5000, 0], "level": l1, "thickness": 200, "height": 3})["result"]["id"]
        door = self.project.execute("model.add_door", {"wall": wall, "offset": 2.0, "width": 900})["result"]["id"]
        sid = self.new()
        self.op(sid, "rectangle", p1=[0, 0], p2=[40, 60])                           # a frame cross-section, 40 x 60 mm
        res = self.send(sid, "opening", opening=door)
        self.assertEqual(len(res["result"]["ids"]), 1)
        self.assertTrue(E.alive(self.project, res["result"]["ids"][0]))


class ColourAndParameters(ServerCase):
    """One coloured line colours only its faces; the part colour and the profile's parameters are reachable from the element."""

    def setUp(self):
        super().setUp()
        self.api.post("/api/project/new", {"name": "P", "unit": "mm"})
        self.project = self.app.require()
        from ..profiler.session import STORE
        before = set(STORE._s)
        self.addCleanup(lambda: [STORE.close(i) for i in set(STORE._s) - before])  # the store is shared by the whole test run

    new, op, send, volume = Transfer.new, Transfer.op, Transfer.send, Transfer.volume

    def square(self, colour_one=True):
        sid = self.new()
        self.op(sid, "polyline", points=[[0, 0], [100, 0], [100, 60], [0, 60]], closed=True, constrain=True)
        edges = self.api.get("/api/profiler/%s" % sid)["render"]["edges"]
        if colour_one:
            bottom = next(e for e in edges if max(abs(q[1]) for q in e["points"]) < 1e-9)
            self.op(sid, "color", ids=[bottom["id"]], color="#E8590C")
        return sid

    def runs(self, oid):
        obj = next(o for o in self.api.get("/api/scene")["objects"] if o["id"] == oid)
        return obj.get("layer_colors") or []

    def test_one_coloured_line_colours_only_its_wall_faces(self):
        oid = self.send(self.square(), "extrude", depth=1.0, mode="embedded")["result"]["id"]
        runs = self.runs(oid)
        self.assertTrue(runs, "per-face colours are sent")
        coloured = sum(r["count"] for r in runs if r["color"].upper() == "#E8590C")
        total = sum(r["count"] for r in runs)
        self.assertEqual(coloured, 2)                                               # one wall = two triangles
        self.assertGreater(total, coloured)
        self.assertNotEqual(E.meta_of(self.project, oid).get("color"), "#E8590C")  # the element itself is not recoloured

    def test_colour_survives_a_resync_and_follows_the_geometry(self):
        oid = self.send(self.square(), "extrude", depth=1.0, mode="embedded")["result"]["id"]
        from ..profiler import transfer as T
        T.resync_element(self.project, oid, overrides={})
        self.assertEqual(sum(r["count"] for r in self.runs(oid) if r["color"].upper() == "#E8590C"), 2)

    def test_part_colour_parameter_paints_the_whole_part(self):
        oid = self.send(self.square(False), "extrude", depth=1.0, mode="embedded")["result"]["id"]
        from .. import profile_params as PR
        group = PR.group(self.project, oid)
        self.assertIn("profile:colour", [r.get("key") for r in group["rows"]])
        self.project.execute("model.set_property", {"id": oid, "key": "profile:colour", "value": "#1C7ED6"})
        self.assertEqual(E.m(E.meta_of(self.project, oid), "color").upper(), "#1C7ED6")

    def test_profile_parameters_in_properties_and_in_formulas(self):
        sid = self.new("rectangle")
        oid = self.send(sid, "extrude", depth=0.5, mode="embedded")["result"]["id"]
        from .. import profile_params as PR
        group = PR.group(self.project, oid)
        keys = [r.get("key") for r in group["rows"]]
        self.assertIn("profile:Width", keys)
        before = self.volume(oid)
        self.project.execute("model.set_property", {"id": oid, "key": "profile:Width", "value": 240})
        self.assertAlmostEqual(self.volume(oid), before * 2, 9)
        self.assertAlmostEqual(PR.variables(self.project, oid)["profile_Width"].value, 0.24, 9)
        with self.assertRaises(Exception):
            self.project.execute("model.set_property", {"id": oid, "key": "profile:Width", "value": "wide"})

    def test_depth_label_works_in_a_condition(self):
        oid = self.send(self.new("rectangle"), "extrude", depth=1.0, mode="embedded")["result"]["id"]
        self.project.execute("model.set_property", {"id": oid, "key": "material_rule:condition", "value": "=Depth > 0.5m and profile_Width > 100mm"})
        self.project.execute("model.set_property", {"id": oid, "key": "material_rule:enabled", "value": "true"})

    def test_part_colour_op_checks_the_value(self):
        sid = self.new("rectangle")
        self.op(sid, "part_color", color="#12ab34")
        self.assertEqual(self.api.get("/api/profiler/%s" % sid)["params"]["colour"], "#12AB34")
        status, _ = self.api.call("POST", "/api/profiler/%s/op" % sid, {"op": "part_color", "args": {"color": "red"}})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
