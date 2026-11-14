"""What the client relies on for the issue-sheet features (11-11-2026): the scene tells which part every face is (with its Properties key), a plan point
picks a curtain-wall part, door handles describe the leaf as it is drawn, a drip edge can be added from the edge member it goes on, and Properties carry
the accessory kind and the downpipe's components for the context menus. Every request goes through the real API routes."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

from .harness import ServerCase

RECT = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]


class Contract(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("UI contract")
        self.l0, self.l1, self.lr = (l["id"] for l in self.p.manifest["levels"])

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def scene(self, oid):
        return next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)

    def test_every_face_run_of_an_assembly_carries_the_key_properties_opens(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.l0, height=3000)["id"]
        roof = self.cmd("model.add_roof", points=RECT, level=self.lr, roof="gable", pitch=30, thickness=200)["id"]
        gutter = self.cmd("model.add_gutters", roof=roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.cmd("model.add_downpipe", gutter=gutter, offset=5.0)["id"]
        self.cmd("model.edit_downpipe", id=pipe, action="split", component="c1")             # two sections: a connector between them
        for oid in (cw, pipe):
            runs = self.scene(oid)["components"]
            self.assertTrue(all("part" in r for r in runs))
            for r in runs[:6]:
                props = self.p.properties({"model": oid, "part": r["part"]})              # every key opens that part's own panel
                self.assertEqual(props["ref"], {"model": oid, "part": r["part"]})
        parts = {r["part"] for r in self.scene(pipe)["components"]}
        self.assertTrue(any(p.startswith("conn:") for p in parts) and any(p.startswith("comp:") for p in parts))

    def test_a_plan_point_picks_the_part_under_it(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.l0, height=3000, v_mode="count", v_value=1)["id"]
        got = self.p.pick_part({"id": cw, "point": [1500, 0]})                                 # drawing units (mm), at the plan's cut height
        self.assertEqual(got["part"], "cell:0,0")
        self.assertEqual(self.p.pick_part({"id": cw, "point": [4500, 0], "z": 1.0})["part"], "cell:1,0")
        wall = self.cmd("model.add_wall", start=[0, 2000], end=[6000, 2000], level=self.l0, height=3, thickness=200)["id"]
        with self.assertRaises(BadRequest):
            self.p.pick_part({"id": wall, "point": [100, 2000]})

    def test_a_door_handle_is_the_leaf_as_drawn_turned_about_its_hinge(self):
        cw = self.cmd("model.add_curtainwall", start=[0, 0], end=[6000, 0], level=self.l0, height=3000, h_mode="count", h_value=1)["id"]
        self.cmd("model.set_property", id=cw, key="panel:1:0:type", value="Door")
        wall = self.cmd("model.add_wall", start=[0, 3000], end=[6000, 3000], level=self.l0, height=3, thickness=200)["id"]
        door = self.cmd("model.add_door", wall=wall, offset=2.0, width=900)["id"]
        for oid, key in ((cw, "panel:1:0:open_to"), (door, "open_to")):
            for angle in (35.0, 90.0, 140.0):
                with self.subTest(oid=oid, angle=angle):
                    self.cmd("model.set_property", id=oid, key=key, value=angle)
                    h = self.scene(oid)["door_handles"][0]
                    self.assertTrue(h["open"])
                    if oid == cw:
                        self.assertEqual(f"{h['key']}:open_to", key)                                # the key the 3D grip writes back
                    r = [h["closed"][i] - h["hinge"][i] for i in range(2)]
                    f = [h["free"][i] - h["hinge"][i] for i in range(2)]
                    self.assertAlmostEqual(math.hypot(*r), math.hypot(*f), places=9)            # the leaf keeps its width
                    turned = math.degrees(abs(math.atan2(r[0] * f[1] - r[1] * f[0], r[0] * f[0] + r[1] * f[1])))
                    self.assertAlmostEqual(turned, angle, places=6)                            # and stands open by exactly the stored angle

    def test_a_drip_edge_is_added_from_the_edge_member_it_goes_on(self):
        roof = self.cmd("model.add_roof", points=RECT, level=self.lr, roof="gable", pitch=30, thickness=200)["id"]
        member = self.cmd("model.add_roof_accessory", roof=roof, kind="roof_edge", edge="2")["id"]
        props = self.p.properties({"model": member})
        self.assertEqual(props["accessory"], "roof_edge")
        drip = self.cmd("model.add_roof_accessory", member=member, kind="drip_edge")["id"]
        dm = E.meta_of(self.p, drip)
        self.assertEqual((E.m(dm, "roof_host"), E.m(dm, "edge"), E.m(dm, "category")), (roof, 1, "flashing"))
        self.assertEqual(self.p.properties({"model": drip})["accessory"], "drip_edge")
        wall = self.cmd("model.add_wall", start=[0, 0], end=[1000, 0], level=self.l0, height=3, thickness=200)["id"]
        with self.assertRaises(BadRequest):
            self.cmd("model.add_roof_accessory", member=wall, kind="drip_edge")

    def test_the_plan_offers_each_curtain_wall_corner_where_it_is(self):
        run = self.cmd("model.add_curtainwall", points=[[0, 0], [6000, 0], [6000, 4000]], level=self.l0, height=3000)["ids"]
        items = {i["id"]: i for i in E.plan_items(self.p, self.l0, 1000.0) if i["category"] == "curtainwall"}
        corner = items[run[0]]["cw_corners"]
        self.assertEqual(len(corner), 1)
        self.assertEqual((corner[0]["endpoint"], corner[0]["other"], corner[0]["point"]), ("end", run[1], [6000.0, 0.0]))
        self.assertAlmostEqual(corner[0]["angle"], 90.0)
        self.cmd("model.set_curtainwall_corner", id=run[0], end="end", type="V")
        self.assertEqual(next(i for i in E.plan_items(self.p, self.l0, 1000.0) if i["id"] == run[1])["cw_corners"][0]["type"], "V")      # one corner, seen from both walls

    def test_a_downpipe_lists_its_components_for_the_editor(self):
        roof = self.cmd("model.add_roof", points=RECT, level=self.lr, roof="gable", pitch=30, thickness=200)["id"]
        gutter = self.cmd("model.add_gutters", roof=roof, edges=[0], downpipe=False)["gutters"][0]
        pipe = self.cmd("model.add_downpipe", gutter=gutter, offset=5.0)["id"]
        self.cmd("model.edit_downpipe", id=pipe, action="add", kind="shoe")
        items = self.p.properties({"model": pipe})["assembly"]
        self.assertEqual([i["kind"] for i in items], ["pipe", "shoe"])
        self.assertEqual(items[1]["label"], "Shoe")


class OverHttp(ServerCase):
    def test_double_click_routes_answer_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Pick", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        cw = a.cmd("model.add_curtainwall", {"points": [[0, 0], [6000, 0], [6000, 4000]], "level": level, "height": 3000, "v_mode": "count", "v_value": 1})["result"]
        self.assertEqual(len(cw["ids"]), 2)                                                      # a connected run drawn in one go
        got = a.post("/api/curtainwall/pick", {"id": cw["ids"][0], "point": [1500, 0]})
        self.assertEqual(got["part"], "cell:0,0")
        props = a.post("/api/properties", {"model": cw["ids"][0], "part": got["part"]})
        self.assertEqual(props["ref"]["part"], "cell:0,0")
        self.assertEqual(a.call("POST", "/api/curtainwall/pick", {"id": cw["ids"][0], "point": [1500]})[0], 400)


if __name__ == "__main__":
    unittest.main()
