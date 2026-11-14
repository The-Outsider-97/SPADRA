"""Curtain walls that know their surroundings (issue sheet 11-11-2026, items 3 and 4): parts that can be picked and edited one by one, a base that stands on
the floor's top surface, a top attached to a roof, ends hosted by walls, connected runs drawn in one go, the four corners (L, Quad, Trapezoid, V) and door
leaves opened in 3D."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import attachments as AT
from applications.SPADRA import curtainwall as CW
from applications.SPADRA import elements as E
from applications.SPADRA import geometry as G
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Curtain wall connections")
        self.l0, self.l1, self.lr = (l["id"] for l in self.p.manifest["levels"])

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def cw(self, **kw):
        params = {"start": [0, 0], "end": [6000, 0], "level": self.l0, "height": 3000, **kw}
        return self.cmd("model.add_curtainwall", **params)

    def meta(self, oid):
        return E.meta_of(self.p, oid)

    def scene(self, oid):
        return next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)

    def closed(self, oid):
        o = self.scene(oid)
        return G.is_closed((o["vertices"], o["faces"]))


class Parts(Case):
    def test_every_face_belongs_to_a_named_part_and_a_point_picks_the_part_under_it(self):
        oid = self.cw()["id"]
        o = self.scene(oid)
        self.assertEqual(sum(r["count"] for r in o["components"]), len(o["faces"]))
        ids = {r["id"] for r in o["components"]}
        self.assertTrue({"v:start", "v:end", "h:bottom", "h:top", "cell:0,0"} <= ids)
        sp = CW.spec(self.meta(oid))
        V, Hh = CW.axes(sp, "v"), CW.axes(sp, "h")
        mid_u = (V[0] + V[1]) / 2
        self.assertEqual(CW.pick(self.p, oid, [mid_u, 0.0, 1.0])["part"], "cell:0,0")           # a point in the first field, 1 m up
        self.assertEqual(CW.pick(self.p, oid, [V[1], 0.0, 1.0])["part"], "v:0")                  # on the first mullion: the mullion, not the field
        self.assertEqual(CW.pick(self.p, oid, [mid_u, 0.0, Hh[1]])["part"], "h:0")
        self.assertEqual(CW.pick(self.p, oid, [mid_u, 0.0, 2.5])["part"], "cell:0,1")

    def test_a_picked_field_and_member_have_their_own_properties_that_edit_the_wall(self):
        oid = self.cw()["id"]
        panel = self.p.properties({"model": oid, "part": "cell:1,0"})
        keys = {r.get("key") for r in panel["top"]}
        self.assertIn("panel:1:0:type", keys)
        self.assertIn("Panel 2.1", panel["title"])
        self.cmd("model.set_property", id=oid, key="panel:1:0:type", value="Opaque panel")
        self.assertEqual(CW.cell_spec(CW.spec(self.meta(oid)), next(c for c in CW.cells(CW.spec(self.meta(oid))) if c["key"] == "1,0"))["type"], "Opaque panel")
        member = self.p.properties({"model": oid, "part": "v:0"})
        self.assertIn("Mullion 1", member["title"])
        self.cmd("model.set_property", id=oid, key="line:v:0", value="removed")
        self.assertTrue(CW.lines(CW.spec(self.meta(oid)), "v")[0]["removed"])
        self.p.undo()
        self.assertFalse(CW.lines(CW.spec(self.meta(oid)), "v")[0]["removed"])


class Floor(Case):
    def test_the_base_stands_on_the_floors_top_surface_and_follows_it(self):
        floor = self.cmd("model.add_floor", points=[[-1000, -1000], [8000, -1000], [8000, 5000], [-1000, 5000]], level=self.l0, thickness=300)["id"]
        self.cmd("model.set_property", id=floor, key="offset", value=200)                         # its top 200 mm above Level 0
        oid = self.cw()["id"]
        wall = self.cmd("model.add_wall", start=[0, 3000], end=[6000, 3000], level=self.l0, height=3, thickness=200)["id"]
        self.assertAlmostEqual(CW.base_z(self.p, self.meta(oid)), 0.2, places=9)
        self.assertAlmostEqual(self.p.model.world_bounds(oid)[0][2], 0.2, places=9)
        self.assertAlmostEqual(self.p.model.world_bounds(wall)[0][2], 0.2, places=9)              # the same for a wall
        self.cmd("model.set_property", id=floor, key="offset", value=350)
        self.assertAlmostEqual(self.p.model.world_bounds(oid)[0][2], 0.35, places=9)
        self.assertAlmostEqual(self.p.model.world_bounds(wall)[0][2], 0.35, places=9)
        self.cmd("model.set_property", id=oid, key="base_offset", value=50)                        # an offset of its own is kept above the floor
        self.assertAlmostEqual(self.p.model.world_bounds(oid)[0][2], 0.4, places=9)
        self.cmd("model.delete", id=floor)
        self.assertAlmostEqual(self.p.model.world_bounds(oid)[0][2], 0.4, places=9)               # the floor gone: it stays where it was
        self.assertIsNone(AT.base_attachment(self.meta(oid)))

    def test_a_floor_of_another_level_or_beside_it_is_not_its_floor(self):
        self.cmd("model.add_floor", points=[[-1000, -1000], [8000, -1000], [8000, 5000], [-1000, 5000]], level=self.l1, thickness=300)
        self.cmd("model.add_floor", points=[[10000, 0], [12000, 0], [12000, 2000], [10000, 2000]], level=self.l0, thickness=300)
        oid = self.cw()["id"]
        self.assertIsNone(AT.base_attachment(self.meta(oid)))


class Roof(Case):
    def setUp(self) -> None:
        super().setUp()
        self.roof = self.cmd("model.add_roof", points=[[-500, -2000], [6500, -2000], [6500, 2000], [-500, 2000]], level=self.l1, roof="gable", pitch=30, thickness=200)["id"]

    def test_the_top_follows_the_roofs_underside_and_nothing_enters_the_roof(self):
        oid = self.cw(start=[0, 0], end=[6000, 0], height=3000)["id"]
        self.cmd("model.attach_wall", ids=[oid, self.roof], to="roof")
        self.assertIsNone(self.meta(oid).get(f"{NS}:top_profile"))                                  # along the ridge the underside is level
        self.cmd("model.detach_wall", ids=[oid])
        across = self.cw(start=[3000, -1800], end=[3000, 1800], height=3000)["id"]
        self.cmd("model.attach_wall", ids=[across, self.roof], to="roof")
        prof = self.meta(across)[f"{NS}:top_profile"]
        self.assertEqual(len(prof), 3)                                                              # up to the ridge and down again
        self.assertTrue(self.closed(across))
        facets = E.roof_facets(self.p, self.meta(self.roof))
        for x, y, z in self.scene(across)["vertices"]:
            under = min(pl[0] * x + pl[1] * y + pl[2] for ring, pl in facets)                        # the underside is the lower of the two slopes
            self.assertLessEqual(z, max(pl[0] * x + pl[1] * y + pl[2] for ring, pl in facets) + 1e-6)
        ids = {r["id"] for r in self.scene(across)["components"]}
        self.assertIn("top", ids)
        self.assertNotIn("h:top", ids)
        self.cmd("model.set_property", id=self.roof, key="pitch", value=35)                         # the roof changes: the top follows
        self.assertNotEqual(self.meta(across)[f"{NS}:top_profile"], prof)
        self.assertTrue(self.closed(across))

    def test_a_door_field_the_roof_comes_down_into_is_refused(self):
        across = self.cw(start=[3000, -1800], end=[3000, 1800], height=3000, h_mode="count", h_value=0)["id"]
        self.cmd("model.set_property", id=across, key="panel:0:0:type", value="Door")
        with self.assertRaises(BadRequest):
            self.cmd("model.attach_wall", ids=[across, self.roof], to="roof")


class Hosts(Case):
    def test_an_end_on_a_wall_stays_on_its_face_when_the_wall_moves(self):
        wall = self.cmd("model.add_wall", start=[6000, -2000], end=[6000, 2000], level=self.l0, height=3, thickness=300)["id"]
        oid = self.cw(start=[0, 0], end=[6000, 0])["id"]
        self.assertEqual(self.meta(oid)[f"{NS}:end_hosts"]["end"]["wall"], wall)
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:end"][0], 5.85, places=9)                     # pulled back to the face toward it
        self.cmd("model.move", id=wall, dx=0.5, dy=0)                                             # model.move takes metres
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:end"][0], 6.35, places=9)
        self.cmd("model.set_property", id=wall, key="thickness", value=200)
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:end"][0], 6.4, places=9)
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:start"][0], 0.0, places=9)                    # the other end stays


class Corners(Case):
    def run_of(self, pts, **kw):
        return self.cw(points=pts, start=None, end=None, **kw)["ids"]

    def test_a_run_drawn_in_one_go_is_joined_and_each_corner_has_one_post(self):
        a, b, c = self.run_of([[0, 0], [5000, 0], [5000, 4000], [9000, 4000]])
        self.assertEqual(CW.links_of(self.meta(a))["end"], {"id": b, "end": "start"})
        posts = [q for oid in (a, b, c) for q in CW.spec(self.meta(oid))["corner_posts"]]
        self.assertEqual(len(posts), 2)                                                             # two junctions, one post each
        for oid in (a, b, c):
            self.assertTrue(self.closed(oid))
        j = CW._junction(self.p, a, "end")
        self.assertAlmostEqual(j["angle"], 90.0, places=9)
        self.assertEqual(CW.corner_type(j), "quad")
        ends = CW.spec(self.meta(a))["end_cond"]["end"]
        self.assertTrue(ends["suppress"])
        self.assertAlmostEqual(ends["setback"], 0.045, places=9)                                    # half the other wall's 90 mm frame
        roles = [q["id"] for q in CW.members(self.p, self.meta(a)) if q["kind"] == "frame"]
        self.assertNotIn("v:end", roles)                                                            # its own perimeter member is given up to the post

    def test_the_four_corners_are_built_where_their_angle_allows_and_refused_where_it_does_not(self):
        for angle, allowed in ((90, {"L", "quad", "trapezoid", "V"}), (45, {"L", "quad", "V"}), (160, {"trapezoid", "V"})):
            with self.subTest(angle=angle):
                t = math.radians(180 - angle)
                a, b = self.run_of([[0, 0], [4000, 0], [4000 + 3000 * math.cos(t), 3000 * math.sin(t)]])
                j = CW._junction(self.p, a, "end")
                self.assertAlmostEqual(j["angle"], angle, places=6)
                for kind in ("L", "quad", "trapezoid", "V"):
                    if kind in allowed:
                        self.cmd("model.set_curtainwall_corner", id=a, end="end", type=kind)
                        post = CW.spec(self.meta(min(a, b)))["corner_posts"][0]
                        self.assertEqual(post["type"], kind)
                        poly = [tuple(q) for q in post["poly"]]
                        self.assertTrue(G.is_simple_polygon(poly) and abs(G.signed_area(poly)) > 1e-5)
                        self.assertTrue(self.closed(a) and self.closed(b))
                    else:
                        with self.assertRaises(BadRequest):
                            self.cmd("model.set_curtainwall_corner", id=a, end="end", type=kind)
                for oid in (a, b):
                    self.cmd("model.delete", id=oid)

    def test_a_corner_is_kept_through_edits_saving_and_undo(self):
        a, b = self.run_of([[0, 0], [4000, 0], [4000, 3000]])
        self.cmd("model.set_curtainwall_corner", id=b, end="start", type="L", leg_a=150)
        self.cmd("model.set_property", id=a, key="length", value=3500)                              # the shared end moves: the neighbour's end goes with it
        self.assertAlmostEqual(math.dist(self.meta(a)[f"{NS}:end"], self.meta(b)[f"{NS}:start"]), 0.0, places=9)
        self.assertEqual(CW.corner_type(CW._junction(self.p, a, "end")), "L")
        self.cmd("model.move", id=b, dx=-0.2, dy=0)
        self.assertAlmostEqual(math.dist(self.meta(a)[f"{NS}:end"], self.meta(b)[f"{NS}:start"]), 0.0, places=9)
        self.assertEqual(CW.corner_type(CW._junction(self.p, a, "end")), "L")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corners.spadra"
            self.p.save(path)
            again = Project.open(path)
            self.assertEqual(CW.corner_type(CW._junction(again, a, "end")), "L")
            self.assertEqual(CW.spec(E.meta_of(again, a))["corner_posts"] + CW.spec(E.meta_of(again, b))["corner_posts"],
                             CW.spec(self.meta(a))["corner_posts"] + CW.spec(self.meta(b))["corner_posts"])
        self.p.undo()
        self.p.undo()
        self.p.undo()
        self.assertEqual(CW.corner_type(CW._junction(self.p, a, "end")), "quad")

    def test_walls_in_a_line_share_one_member(self):
        a, b = self.run_of([[0, 0], [3000, 0], [6000, 0]])
        owner, other = (a, b) if a < b else (b, a)
        self.assertEqual(CW.spec(self.meta(owner))["corner_posts"], [])
        self.assertTrue(CW.spec(self.meta(other))["end_cond"][("start" if other == b else "end")]["suppress"])


class DoorOpening(Case):
    def test_a_curtain_wall_door_is_shown_open_in_3d_at_its_opening_angle(self):
        oid = self.cw(h_mode="count", h_value=1)["id"]
        self.cmd("model.set_property", id=oid, key="panel:1:0:type", value="Door")
        closed = [q for q in CW.members(self.p, self.meta(oid)) if q["kind"] == "door"][0]["mesh"][0]
        vol = self.p.model.volume(oid, unit="m")
        self.cmd("model.set_property", id=oid, key="panel:1:0:open_to", value=90)
        opened = [q for q in CW.members(self.p, self.meta(oid)) if q["kind"] == "door"][0]["mesh"][0]
        us = [v[0] for v in opened]
        self.assertLess(max(us) - min(us), 0.06)                                                     # a quarter turn: it stands across the wall
        self.assertAlmostEqual(self.p.model.volume(oid, unit="m"), vol, places=9)
        handle = self.scene(oid)["door_handles"][0]
        self.assertTrue(handle["open"])
        self.assertAlmostEqual(handle["angle"], 90.0)
        self.cmd("model.set_property", id=oid, key="panel:1:0:show_open", value="closed")
        again = [q for q in CW.members(self.p, self.meta(oid)) if q["kind"] == "door"][0]["mesh"][0]
        self.assertEqual([[round(c, 9) for c in v] for v in again], [[round(c, 9) for c in v] for v in closed])

    def test_a_door_in_a_wall_is_turned_about_its_hinge_the_same_way(self):
        wall = self.cmd("model.add_wall", start=[0, 0], end=[6000, 0], level=self.l0, height=3, thickness=200)["id"]
        door = self.cmd("model.add_door", wall=wall, offset=2.0, width=900)["id"]
        before = self.scene(door)["vertices"]
        self.cmd("model.set_property", id=door, key="open_to", value=90)
        after = self.scene(door)["vertices"]
        xs = [v[0] for v in after]
        self.assertLess(max(xs) - min(xs), 0.1)
        self.assertEqual(len(self.scene(door)["door_handles"]), 1)
        self.cmd("model.set_property", id=door, key="show_open", value="closed")
        self.assertEqual([[round(c, 9) for c in v] for v in self.scene(door)["vertices"]], [[round(c, 9) for c in v] for v in before])


if __name__ == "__main__":
    unittest.main()
