"""Sections, elevations and schedules against the real SGEO engine. The numbers asserted are the ones the building's parameters imply
(a wall cut through its middle is exactly thickness × height; a door takes exactly its width and height out of an elevation), so a
projection that was only plausible-looking would fail here."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import documentation as D
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict, NotFound


def area(loop):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:] + loop[:1])) / 2)


def tri_area(tri):
    x = tri
    return abs((x[2] - x[0]) * (x[5] - x[1]) - (x[4] - x[0]) * (x[3] - x[1])) / 2


class Room(unittest.TestCase):
    """A 6 m × 4 m room (200 mm concrete walls, 3 m high); a 900 × 2100 door at x = 1.5 m and a 1200 × 1200 window (sill 900) at x = 4.5 m in the south wall."""

    def setUp(self) -> None:
        self.p = Project.create("Room")                             # drawing unit mm
        self.level = self.p.manifest["levels"][0]["id"]
        corners = [(0, 0), (6000, 0), (6000, 4000), (0, 4000)]
        self.walls = [self.cmd("model.add_wall", start=list(a), end=list(b), level=self.level)["id"] for a, b in zip(corners, corners[1:] + corners[:1])]
        self.door = self.cmd("model.add_door", wall=self.walls[0], point=[1500, 0], width=900, height=2100)["id"]
        self.window = self.cmd("model.add_window", wall=self.walls[0], point=[4500, 0], width=1200, height=1200, sill=900)["id"]

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def section(self, a, b, **extra):
        vid = self.cmd("view.add_section", a=list(a), b=list(b), **extra)["id"]
        return vid, D.project_view(self.p, self.p.view(vid), 1000.0)

    def cuts(self, projection):
        return {i["id"]: [area(loop) for loop in i["cut"]] for i in projection["items"] if i["cut"]}

    def elevation(self, side):
        view = next(v for v in self.p.manifest["views"] if v.get("side") == side)
        return D.project_view(self.p, view, 1000.0)


class Sections(Room):
    def test_a_wall_cut_through_its_middle_is_exactly_thickness_times_height(self):
        _, pr = self.section((3000, -2000), (3000, 6000))
        areas = self.cuts(pr)
        self.assertEqual(len(areas), 2)                              # the south and the north wall; nothing else crosses x = 3000
        for value in [a for v in areas.values() for a in v]:
            self.assertAlmostEqual(value, 200 * 3000, places=3)      # mm²

    def test_the_cut_through_a_door_leaves_the_wall_above_it_and_the_leaf_in_the_gap(self):
        _, pr = self.section((1500, -2000), (1500, 6000))
        areas = self.cuts(pr)
        self.assertAlmostEqual(sum(areas[self.walls[0]]), 200 * (3000 - 2100), places=3)       # only the lintel is left in the wall
        self.assertEqual(len(areas[self.walls[0]]), 1)
        self.assertAlmostEqual(sum(areas[self.door]), 50 * 2100, places=3)                    # the 50 mm leaf, 2100 high

    def test_the_cut_through_a_window_has_a_hole_and_the_glazing_fills_it_partly(self):
        _, pr = self.section((4500, -2000), (4500, 6000))
        areas = self.cuts(pr)
        wall = areas[self.walls[0]]
        self.assertEqual(len(wall), 2)                               # below the sill and above the head: two pieces
        self.assertAlmostEqual(sum(wall), 200 * (900 + 900), places=3)
        self.assertAlmostEqual(sum(areas[self.window]), 30 * 1200, places=3)

    def test_a_cut_through_a_wall_with_an_opening_inside_it_is_one_loop_with_a_hole(self):
        self.cmd("model.set_property", id=self.window, key="sill", value=900)
        w = self.cmd("model.add_wall", start=[0, 8000], end=[6000, 8000], level=self.level)["id"]
        self.cmd("model.add_window", wall=w, point=[3000, 8000], width=1000, height=1200, sill=900)
        _, pr = self.section((3000, 7000), (3000, 9000))
        loops = next(i for i in pr["items"] if i["id"] == w)["cut"]
        self.assertEqual(len(loops), 2)                              # the wall below the window, and above it
        self.assertAlmostEqual(sum(area(l) for l in loops), 200 * 1800, places=3)

    def test_the_section_only_cuts_what_is_inside_its_own_width(self):
        _, pr = self.section((3000, 1000), (3000, 3000))             # stops short of both walls
        self.assertEqual(self.cuts(pr), {})
        _, pr = self.section((3000, -2000), (3000, 500))             # reaches the south wall only
        self.assertEqual(list(self.cuts(pr)), [self.walls[0]])

    def test_the_view_looks_to_the_left_of_the_line_and_flipping_turns_it_round(self):
        vid, pr = self.section((3000, -2000), (3000, 6000))          # a → b points north; left of that is west
        view = self.p.view(vid)
        self.assertEqual(D.compass(D.frame_of(view).fx, D.frame_of(view).fy), "west")
        beyond_west = {i["id"] for i in pr["items"] if i["tris"]}
        self.assertIn(self.walls[3], beyond_west)                    # the west wall's inner face is what is seen
        self.assertNotIn(self.walls[1], beyond_west)
        self.cmd("view.flip_section", id=vid)
        flipped = D.project_view(self.p, self.p.view(vid), 1000.0)
        self.assertEqual(D.compass(D.frame_of(self.p.view(vid)).fx, D.frame_of(self.p.view(vid)).fy), "east")
        seen = {i["id"] for i in flipped["items"] if i["tris"]}
        self.assertIn(self.walls[1], seen)
        self.assertNotIn(self.walls[3], seen)
        self.assertEqual(sorted(round(a) for v in self.cuts(flipped).values() for a in v), sorted(round(a) for v in self.cuts(pr).values() for a in v))

    def test_what_is_beyond_the_cut_is_the_faces_that_face_the_viewer_and_nothing_else(self):
        _, pr = self.section((3000, -2000), (3000, 6000))
        west = next(i for i in pr["items"] if i["id"] == self.walls[3])
        # the inner face of the west wall runs between the inner faces of the south and north walls, 3800 mm; its two mitre faces slant
        # towards the viewer and each shows 200 mm × 3000 mm (they sit behind the cut walls, which paint over them)
        self.assertAlmostEqual(sum(tri_area(t) for t in west["tris"]), (3800 + 2 * 200) * 3000, places=2)
        self.assertEqual(len(west["edges"]), 4 + 3 + 3)              # each face as its outline only (no triangulation diagonal); a mitre face shares one edge with the inner face

    def test_the_depth_limit_hides_what_is_further_away(self):
        _, near = self.section((3000, -2000), (3000, 6000), depth=2000)       # the west wall is 3000 beyond the cut
        self.assertNotIn(self.walls[3], {i["id"] for i in near["items"] if i["tris"]})
        _, far = self.section((3000, -2000), (3000, 6000), depth=3200)
        self.assertIn(self.walls[3], {i["id"] for i in far["items"] if i["tris"]})

    def test_items_are_ordered_far_to_near(self):
        _, pr = self.section((3000, -2000), (3000, 6000))
        depths = [i["depth"] for i in pr["items"]]
        self.assertEqual(depths, sorted(depths, reverse=True))

    def test_the_section_follows_the_model_when_it_changes(self):
        vid, _ = self.section((3000, -2000), (3000, 6000))
        self.cmd("model.set_property", id=self.walls[0], key="height", value=4.0)
        pr = D.project_view(self.p, self.p.view(vid), 1000.0)
        self.assertAlmostEqual(max(p[1] for l in self.cuts_loops(pr, self.walls[0]) for p in l), 4000.0, places=3)

    def cuts_loops(self, pr, oid):
        return next(i for i in pr["items"] if i["id"] == oid)["cut"]


class Elevations(Room):
    def test_a_new_project_starts_with_the_four_elevations(self):
        views = [(v["name"], v["side"]) for v in self.p.manifest["views"] if v["kind"] == "elevation"]
        self.assertEqual(views, [("East", "east"), ("North", "north"), ("South", "south"), ("West", "west")])

    def test_the_south_elevation_shows_the_door_and_window_openings_exactly(self):
        pr = self.elevation("south")
        front = next(i for i in pr["items"] if i["id"] == self.walls[0])
        # the outer face runs to the outer corners, 100 mm past each end of the centre line, because the walls meet in mitres
        self.assertAlmostEqual(sum(tri_area(t) for t in front["tris"]), (6200 * 3000 - 900 * 2100 - 1200 * 1200), places=2)
        length = sum(math.hypot(e[2] - e[0], e[3] - e[1]) for e in front["edges"])
        # outline 2 × (6200 + 3000), the door notch adds 2 × 2100, the window hole 2 × (1200 + 1200)
        self.assertAlmostEqual(length, 18400 + 4200 + 4800, places=2)

    def test_the_door_and_the_window_are_drawn_in_their_openings(self):
        pr = self.elevation("south")
        by_id = {i["id"]: i for i in pr["items"]}
        for oid, (x0, x1, z0, z1) in ((self.door, (1050, 1950, 0, 2100)), (self.window, (3900, 5100, 900, 2100))):
            xs = [c for t in by_id[oid]["tris"] for c in t[0::2]]
            zs = [c for t in by_id[oid]["tris"] for c in t[1::2]]
            self.assertAlmostEqual(min(xs), x0, places=2); self.assertAlmostEqual(max(xs), x1, places=2)
            self.assertAlmostEqual(min(zs), z0, places=2); self.assertAlmostEqual(max(zs), z1, places=2)

    def test_left_and_right_follow_the_viewer(self):
        """From the south the door (at x = 1.5 m) is on the viewer's left of the window; from the north it is on the right."""
        south = {i["id"]: i for i in self.elevation("south")["items"]}
        north = {i["id"]: i for i in self.elevation("north")["items"]}
        mid = lambda it: sum(it["tris"][0][0::2]) / 3                 # noqa: E731
        self.assertLess(mid(south[self.door]), mid(south[self.window]))
        self.assertGreater(mid(north[self.door]), mid(north[self.window]))

    def test_the_far_wall_is_painted_before_the_near_one(self):
        order = [i["id"] for i in self.elevation("south")["items"]]
        self.assertLess(order.index(self.walls[2]), order.index(self.walls[0]))      # north wall (far) before the south wall (near)
        self.assertLess(order.index(self.walls[2]), order.index(self.door))

    def test_levels_are_reported_for_the_level_lines(self):
        levels = self.elevation("east")["levels"]
        self.assertEqual([(l["name"], l["z"]) for l in levels], [("Level 01", 0.0), ("Level 02", 3200.0), ("Roof Level", 6400.0)])

    def test_the_view_changes_when_the_model_does(self):
        before = self.elevation("south")["bounds"]
        self.cmd("model.set_property", id=self.walls[0], key="height", value=5.0)
        self.assertAlmostEqual(self.elevation("south")["bounds"][1][1], 5000.0, places=3)
        self.assertNotEqual(before, self.elevation("south")["bounds"])

    def test_an_empty_model_projects_to_nothing_rather_than_failing(self):
        empty = Project.create("Empty")
        view = next(v for v in empty.manifest["views"] if v["kind"] == "elevation")
        pr = D.project_view(empty, view, 1000.0)
        self.assertEqual((pr["items"], pr["bounds"]), ([], None))
        self.assertEqual(empty.drawing_data(view["id"])["bounds"], None)


class FloorsRoofsAndBeams(Room):
    def test_a_floor_slab_and_a_flat_roof_are_cut_with_their_exact_thickness(self):
        floor = self.cmd("model.add_floor", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.level, thickness=200)["id"]
        roof = self.cmd("model.add_roof", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.level, roof="flat", thickness=300)["id"]
        _, pr = self.section((3000, -2000), (3000, 6000))
        areas = self.cuts(pr)
        self.assertAlmostEqual(sum(areas[floor]), 4000 * 200, places=2)
        self.assertAlmostEqual(sum(areas[roof]), 4000 * 300, places=2)

    def test_a_gable_roof_is_cut_into_its_real_profile(self):
        roof = self.cmd("model.add_roof", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.level, roof="gable", pitch=30, thickness=200)["id"]
        _, pr = self.section((3000, -2000), (3000, 6000))                  # across the ridge's line? the ridge runs along the long side
        self.assertGreater(len(self.cuts(pr)[roof]), 0)
        volume = self.p.model.volume(roof, unit="m")
        # a cut perpendicular to the ridge is the roof's cross-section; its area times the roof's length is the roof's volume
        self.assertAlmostEqual(sum(self.cuts(pr)[roof]) / 1e6 * 6.0, volume, places=2)

    def test_a_beam_is_cut_as_its_exact_rectangle(self):
        beam = self.cmd("model.add_beam", start=[0, 2000], end=[6000, 2000], level=self.level, top=3.0, width=300, depth=400)["id"]
        _, pr = self.section((3000, -2000), (3000, 6000))
        self.assertAlmostEqual(sum(self.cuts(pr)[beam]), 300 * 400, places=2)


class Schedules(Room):
    def test_the_door_schedule_lists_the_real_door(self):
        data = D.schedule_data(self.p, "door")
        self.assertEqual(data["count"], 1)
        row = data["rows"][0]
        self.assertEqual((row["no"], row["level"], row["width"], row["height"], row["area"]), ("D01", "Level 01", 900.0, 2100.0, 1.89))
        self.assertEqual(data["totals"]["area"], 1.89)

    def test_the_window_schedule_has_sill_and_head(self):
        row = D.schedule_data(self.p, "window")["rows"][0]
        self.assertEqual((row["sill"], row["head"], row["width"], row["height"]), (900.0, 2100.0, 1200.0, 1200.0))

    def test_the_wall_schedule_nets_off_the_openings_and_counts_them(self):
        data = D.schedule_data(self.p, "wall")
        south = next(r for r in data["rows"] if r["id"] == self.walls[0])
        self.assertEqual((south["gross"], south["openings"], south["net"], south["doors"], south["windows"]), (18.0, 3.33, 14.67, 1, 1))
        self.assertAlmostEqual(south["volume"], 0.2 * 14.67, places=3)           # the exact volume agrees with the area × thickness
        self.assertEqual(data["totals"]["doors"], 1)
        self.assertAlmostEqual(data["totals"]["length"], 20.0, places=3)

    def test_rows_follow_the_edits(self):
        self.cmd("model.set_property", id=self.door, key="width", value=1000)
        self.assertEqual(D.schedule_data(self.p, "door")["rows"][0]["width"], 1000.0)
        self.cmd("model.delete", id=self.door)
        self.assertEqual(D.schedule_data(self.p, "door")["count"], 0)
        self.assertEqual(D.schedule_data(self.p, "wall")["rows"][0]["doors"] + D.schedule_data(self.p, "wall")["rows"][1]["doors"], 0)

    def test_numbering_is_by_level_then_position_not_by_creation_order(self):
        upper = self.p.manifest["levels"][1]["id"]
        w2 = self.cmd("model.add_wall", start=[0, 0], end=[6000, 0], level=upper)["id"]
        d2 = self.cmd("model.add_door", wall=w2, point=[500, 0], width=900, height=2100)["id"]
        d3 = self.cmd("model.add_door", wall=self.walls[2], point=[3000, 4000], width=900, height=2100)["id"]
        rows = D.schedule_data(self.p, "door")["rows"]
        self.assertEqual([(r["no"], r["level"]) for r in rows], [("D01", "Level 01"), ("D02", "Level 01"), ("D03", "Level 02")])
        self.assertEqual(rows[2]["id"], d2)
        self.assertEqual(rows[1]["id"], d3)

    def test_floors_roofs_and_beams_have_schedules_with_exact_volumes(self):
        f = self.cmd("model.add_floor", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.level, thickness=200)["id"]
        r = self.cmd("model.add_roof", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.level, roof="gable", pitch=30, thickness=200)["id"]
        b = self.cmd("model.add_beam", start=[0, 2000], end=[6000, 2000], level=self.level, top=3.0, width=300, depth=400)["id"]
        floor = D.schedule_data(self.p, "floor")["rows"][0]
        self.assertEqual((floor["area"], floor["perimeter"], floor["volume"]), (24.0, 20.0, 4.8))
        roof = D.schedule_data(self.p, "roof")["rows"][0]
        self.assertEqual((roof["type"], roof["pitch"]), ("Gable Roof", 30.0))
        self.assertAlmostEqual(roof["volume"], self.p.model.volume(r, unit="m"), places=3)
        beam = D.schedule_data(self.p, "structural")["rows"][0]
        self.assertEqual((beam["length"], beam["width"], beam["depth"], beam["volume"]), (6.0, 300.0, 400.0, 0.72))

    def test_an_unknown_category_is_refused(self):
        with self.assertRaises(BadRequest):
            D.schedule_data(self.p, "furniture")

    def test_the_csv_has_units_in_the_headers_quotes_what_needs_it_and_ends_with_the_totals(self):
        self.cmd("model.set_property", id=self.door, key="name", value='Front, "main" door')
        csv = D.schedule_csv(D.schedule_data(self.p, "door"))
        lines = csv.strip().split("\r\n")
        self.assertEqual(lines[0], "No.,Name,Level,Host wall,Width (mm),Height (mm),Area (m²),Material,Hinge,Swing,NL-SfB")
        self.assertIn('"Front, ""main"" door"', lines[1])
        self.assertTrue(lines[-1].startswith("Total,"))
        self.assertIn("1.89", lines[-1])


class ViewCommands(Room):
    def test_a_section_is_named_in_order_and_gets_its_own_drawing(self):
        a = self.cmd("view.add_section", a=[0, 1000], b=[6000, 1000])
        b = self.cmd("view.add_section", a=[0, 2000], b=[6000, 2000])
        self.assertEqual((a["name"], b["name"]), ("Section A-A", "Section B-B"))
        self.assertIsNotNone(self.p.drawing(a["id"]))
        self.assertEqual([n["label"] for n in self.p.tree()["sections"][0]["children"][2]["children"]], ["Section A-A", "Section B-B"])

    def test_a_section_line_must_have_length(self):
        with self.assertRaises(BadRequest):
            self.cmd("view.add_section", a=[100, 100], b=[100, 105])

    def test_an_elevation_can_only_exist_once_per_side_and_names_are_unique(self):
        with self.assertRaises(Conflict):
            self.cmd("view.add_elevation", side="north")
        vid = self.cmd("view.add_section", a=[0, 1000], b=[6000, 1000])["id"]
        with self.assertRaises(Conflict):
            self.cmd("view.rename", id=vid, name="north")
        self.cmd("view.rename", id=vid, name="Long section")
        self.assertEqual(self.p.view(vid)["name"], "Long section")

    def test_plans_and_3d_views_cannot_be_edited_or_deleted_as_documentation(self):
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        with self.assertRaises(BadRequest):
            self.cmd("view.delete", id=plan)
        with self.assertRaises(BadRequest):
            self.cmd("view.rename", id=plan, name="x")

    def test_undo_and_redo_bring_a_deleted_view_back_with_its_drawing(self):
        vid = self.cmd("view.add_section", a=[0, 1000], b=[6000, 1000])["id"]
        self.cmd("draw.line", drawing=vid, start=[0, 0], end=[1000, 0])
        self.cmd("view.delete", id=vid)
        with self.assertRaises(NotFound):
            self.p.drawing_data(vid)
        self.p.undo()
        self.assertEqual(len(self.p.drawing_data(vid)["entities"]), 1)
        self.p.redo()
        with self.assertRaises(NotFound):
            self.p.drawing_data(vid)

    def test_the_model_check_still_runs_after_a_view_is_deleted(self):
        vid = self.cmd("view.add_section", a=[0, 1000], b=[6000, 1000])["id"]
        self.cmd("view.delete", id=vid)                      # its drawing is kept in memory for undo, but it is no longer a view
        self.assertTrue(self.p.check()["ok"])
        self.p.undo()
        self.assertTrue(self.p.check()["ok"])

    def test_a_view_on_a_sheet_cannot_be_deleted_until_it_is_removed_from_it(self):
        vid = self.cmd("view.add_section", a=[0, 1000], b=[6000, 1000])["id"]
        sheet = self.cmd("sheet.create", name="S")["id"]
        vp = self.cmd("sheet.place_view", id=sheet, view_id=vid)["id"]
        with self.assertRaises(Conflict):
            self.cmd("view.delete", id=vid)
        self.cmd("sheet.remove_viewport", id=sheet, viewport=vp)
        self.cmd("view.delete", id=vid)

    def test_a_schedule_is_a_view_without_a_drawing_and_can_be_placed_on_a_sheet(self):
        vid = self.cmd("view.add_schedule", category="door")["id"]
        with self.assertRaises(NotFound):
            self.p.drawing(vid)
        with self.assertRaises(Conflict):
            self.cmd("view.add_schedule", category="door")
        sheet = self.cmd("sheet.create", name="S")["id"]
        self.cmd("sheet.place_view", id=sheet, view_id=vid)
        vp = self.p.sheet_data(sheet)["viewports"][0]
        self.assertEqual(vp["schedule"]["count"], 1)
        self.assertTrue(self.p.check()["ok"])

    def test_the_section_mark_is_reported_on_the_plans(self):
        vid = self.cmd("view.add_section", a=[1000, -1000], b=[1000, 5000])["id"]
        plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        marks = self.p.drawing_data(plan)["section_marks"]
        self.assertEqual([(m["id"], m["a"], m["b"], m["looking"]) for m in marks], [(vid, [1000.0, -1000.0], [1000.0, 5000.0], "west")])
        self.assertEqual(self.p.drawing_data(vid)["section_marks"], [])

    def test_the_drawing_of_a_section_carries_the_projection_and_its_bounds(self):
        vid = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]
        data = self.p.drawing_data(vid)
        self.assertEqual(data["projection"]["kind"], "section")
        self.assertEqual(data["bounds"], [[1900.0, 0.0], [6100.0, 3000.0]])
        self.assertEqual(data["model"], [])                           # the plan overlay is for plans only

    def test_annotations_drawn_on_a_section_are_stored_in_its_own_drawing(self):
        vid = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]
        self.cmd("draw.dimension", drawing=vid, p1=[2000, 0], p2=[2000, 3000], dimline_point=[1700, 1500])
        self.assertEqual(len(self.p.drawing_data(vid)["entities"]), 1)
        north = next(v["id"] for v in self.p.manifest["views"] if v.get("side") == "north")
        self.assertEqual(len(self.p.drawing_data(north)["entities"]), 0)

    def test_properties_of_a_section_can_be_read_and_the_depth_edited(self):
        vid = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]
        props = self.p.properties({"view": vid})
        rows = {r.get("key"): r for g in props["groups"] for r in g["rows"] if r.get("key")}
        self.assertEqual((rows["depth"]["value"], rows["looking"]["choices"][0]["label"], rows["looking"]["choices"][1]["label"]), (30.0, "Toward west", "Toward east"))
        self.cmd("view.set_section", id=vid, depth=12.5)
        self.assertEqual(self.p.view(vid)["depth"], 12.5)

    def test_a_failed_view_command_changes_nothing(self):
        before = self.p.revision, [v["id"] for v in self.p.manifest["views"]]
        with self.assertRaises(Conflict):
            self.cmd("view.add_elevation", side="south")
        self.assertEqual((self.p.revision, [v["id"] for v in self.p.manifest["views"]]), before)


class Persistence(Room):
    def test_views_survive_save_and_open_and_an_undone_view_is_not_written(self):
        sec = self.cmd("view.add_section", a=[3000, -2000], b=[3000, 6000])["id"]
        self.cmd("view.add_schedule", category="wall")
        gone = self.cmd("view.add_section", a=[1000, -2000], b=[1000, 6000])["id"]
        self.p.undo()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Room.spadra"
            self.p.save(folder)
            self.assertFalse((folder / "drawings" / f"{gone}.sdrw").exists())
            self.assertTrue((folder / "drawings" / f"{sec}.sdrw").is_file())
            again = Project.open(folder)
        names = [v["name"] for v in again.manifest["views"]]
        self.assertIn("Section A-A", names)
        self.assertIn("Wall Schedule", names)
        self.assertEqual(len([n for n in names if n.startswith("Section")]), 1)
        reopened = D.project_view(again, again.view(sec), 1000.0)
        self.assertEqual(sorted(round(a) for l in [i for i in reopened["items"] if i["cut"]] for a in [area(x) for x in l["cut"]]), [600000, 600000])
        self.assertTrue(again.check()["ok"])


if __name__ == "__main__":
    unittest.main()
