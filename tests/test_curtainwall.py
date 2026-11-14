"""Curtain walls against the real SGEO engine: the spacing rules give the members and fields the numbers imply, the joins never overlap, the profile sizes are the
ones the Stabalux catalogues print (with their page), doors in a field are drawn the same in plan, elevation and 3D, every cut of a member has its own material and
hatch, and the element survives save, reload, undo and redo."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import bimdata as BD
from applications.SPADRA import curtainwall as CW
from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA import sfb as SF
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest

W, H = 6000, 3000          # a 6 m x 3 m wall; its frame is 50 x 90 mm unless a test says otherwise


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Curtain wall")                 # drawing unit mm
        self.level = self.p.manifest["levels"][0]["id"]

    def cmd(self, op, **params):
        return self.p.execute(op, params)["result"]

    def wall(self, **extra):
        params = dict(start=[0, 0], end=[W, 0], level=self.level, height=H)
        params.update(extra)
        return self.cmd("model.add_curtainwall", **params)["id"]

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def spec(self, oid):
        return CW.spec(E.meta_of(self.p, oid))

    def axes_mm(self, oid, d):
        return [round(x * 1000, 3) for x in CW.axes(self.spec(oid), d)]

    def members(self, oid, kind=None, role=None):
        return [q for q in CW.members(self.p, E.meta_of(self.p, oid)) if (kind is None or q["kind"] == kind) and (role is None or q["role"] == role)]

    def volume(self, oid):
        return self.p.model.volume(oid, unit="m")

    def parts_volume(self, oid):
        return sum(q["area"] * (q["length"] if q["kind"] == "frame" else q["thickness"]) for q in self.members(oid))

    def plan(self, f=1000.0, **kw):
        return {i["id"]: i for i in E.plan_items(self.p, self.level, f, **kw)}

    def elevation(self, side):
        view = next(v for v in self.p.manifest["views"] if v.get("side") == side)
        return D.project_view(self.p, view, 1000.0)


class Spacing(Case):
    def test_centre_to_centre_puts_the_remainder_where_align_says(self):
        self.assertEqual(CW.bays(5950, "centre", 1200, "start"), [1200, 1200, 1200, 1200, 1150])
        self.assertEqual(CW.bays(5950, "centre", 1200, "end"), [1150, 1200, 1200, 1200, 1200])
        self.assertEqual([round(b, 6) for b in CW.bays(5950, "centre", 1200, "centre")], [575, 1200, 1200, 1200, 1200, 575])

    def test_an_exact_fit_leaves_no_remainder_bay(self):
        self.assertEqual(CW.bays(6000, "centre", 1200), [1200] * 5)

    def test_maximum_spacing_makes_equal_bays_none_wider_than_the_limit(self):
        b = CW.bays(5950, "max", 1200)
        self.assertEqual(len(b), 5)
        self.assertAlmostEqual(sum(b), 5950)
        self.assertLessEqual(max(b), 1200)
        self.assertAlmostEqual(max(b) - min(b), 0)

    def test_minimum_spacing_makes_equal_bays_none_narrower_than_the_limit(self):
        b = CW.bays(5950, "min", 1200)
        self.assertEqual(len(b), 4)
        self.assertGreaterEqual(min(b), 1200)
        self.assertAlmostEqual(sum(b), 5950)

    def test_a_fixed_number_of_members_makes_one_more_equal_bay(self):
        b = CW.bays(6000, "count", 3)
        self.assertEqual(b, [1500] * 4)
        self.assertEqual(CW.bays(6000, "count", 0), [6000])

    def test_nonsense_spacing_is_refused_with_a_reason(self):
        for mode, value in (("centre", 0), ("max", -5), ("min", 0), ("count", 1.5), ("count", -1)):
            with self.assertRaises(BadRequest, msg=f"{mode} {value}"):
                CW.bays(6000, mode, value)
        with self.assertRaises(BadRequest):
            CW.bays(6000, "centre", 10)                        # 599 members in one direction

    def test_the_wall_applies_each_rule_to_the_axes_between_the_perimeter_members(self):
        oid = self.wall()                                      # 1200 across, 2100 up: the defaults
        self.assertEqual(self.axes_mm(oid, "v"), [25, 1225, 2425, 3625, 4825, 5975])
        self.assertEqual(self.axes_mm(oid, "h"), [25, 2125, 2975])
        oid = self.wall(v_mode="count", v_value=2, h_mode="max", h_value=1000)
        self.assertEqual(self.axes_mm(oid, "v"), [25, round(25 + 5950 / 3, 3), round(25 + 2 * 5950 / 3, 3), 5975])
        self.assertEqual(len(self.axes_mm(oid, "h")), 4)       # 2950 / 1000 -> 3 bays

    def test_members_too_close_for_a_panel_are_refused(self):
        with self.assertRaises(BadRequest) as ctx:
            self.wall(v_mode="centre", v_value=60)
        self.assertIn("a panel needs at least 50 mm", str(ctx.exception))          # names both members, the room left and the room needed
        with self.assertRaises(BadRequest):
            self.wall(frame_width=3000)                        # the frame leaves no room for a panel

    def test_changing_the_rule_through_properties_rebuilds_and_keeps_the_numbers_consistent(self):
        oid = self.wall()
        self.cmd("model.set_property", id=oid, key="v_mode", value="count")
        self.assertEqual(self.spec(oid)["v"]["value"], 3.0)
        self.assertEqual(len(self.axes_mm(oid, "v")), 5)
        self.cmd("model.set_property", id=oid, key="v_value", value=1)
        self.assertEqual(self.axes_mm(oid, "v"), [25, 3000, 5975])
        self.assertEqual(len(CW.cells(self.spec(oid))), 4)


class Frame(Case):
    def test_the_mesh_is_a_closed_solid_and_its_volume_is_the_parts_it_is_made_of(self):
        for join in CW.JOINS:
            oid = self.wall(join=join, corner_radius=8)
            self.assertAlmostEqual(self.volume(oid), self.parts_volume(oid), places=9, msg=join)       # nothing overlaps, nothing is missing

    def test_the_three_joins_cut_the_same_frame_into_different_members(self):
        counts = {}
        volumes = {}
        for join in CW.JOINS:
            oid = self.wall(join=join)
            counts[join] = len(self.members(oid, "frame"))
            volumes[join] = sum(q["area"] * q["length"] for q in self.members(oid, "frame"))
        self.assertEqual(len({round(v, 9) for v in volumes.values()}), 1)                              # the same material, divided differently
        self.assertEqual(counts["mullions"], 6 + 3 * 5)                                                # 6 full-height mullions + 3 rows x 5 bays of transom
        self.assertEqual(counts["transoms"], 3 + 6 * 2)                                                # 3 full-width transoms + 6 columns x 2 rows of mullion
        mitre = self.wall(join="mitre")
        full = [q for q in self.members(mitre, "frame") if q["role"] == "perimeter"]
        self.assertEqual(len(full), 4)

    def test_a_mitre_cuts_the_perimeter_at_45_degrees(self):
        oid = self.wall(join="mitre")
        bottom = next(q for q in self.members(oid, "frame", "perimeter") if q["mesh"][0][0][2] < 0.05 and abs(q["mesh"][0][-1][0] - q["mesh"][0][0][0]) > 1)
        zs = sorted({round(v[2], 6) for v in bottom["mesh"][0]})
        self.assertEqual(zs, [0.0, 0.05])
        left = [v for v in bottom["mesh"][0] if v[0] < 0.1]
        outer = min(v[0] for v in left if abs(v[2]) < 1e-9)
        inner = min(v[0] for v in left if abs(v[2] - 0.05) < 1e-9)
        self.assertAlmostEqual(outer, 0.0)
        self.assertAlmostEqual(inner, 0.05)                    # the outer corner is at the wall's end, the inner one a frame width in: 45 degrees

    def test_corner_radius_rounds_the_section_and_is_limited_by_the_member(self):
        self.assertEqual(len(CW.ring(0.05, 0.09, 0.0)), 4)
        self.assertEqual(len(CW.ring(0.05, 0.09, 0.01)), 4 * (CW.ARC + 1))
        oid = self.wall(corner_radius=10)
        plan = self.plan()[oid]
        mullion_cut = [loop for loop, kind in zip(plan["cut_polygons"], plan["cut_kinds"]) if kind == "frame"]
        self.assertTrue(all(len(loop) >= 16 for loop in mullion_cut))
        with self.assertRaises(BadRequest):
            self.wall(corner_radius=30)                        # more than half the 50 mm face

    def test_a_square_section_has_less_area_than_a_rounded_one_by_the_rounding_removed(self):
        r = 0.01
        square, rounded = CW._area(CW.ring(0.05, 0.09, 0)), CW._area(CW.ring(0.05, 0.09, r))
        exact = (4 - math.pi) * r * r
        self.assertGreater(square - rounded, exact)                                  # the 4-segment arcs cut a little more than a true circle
        self.assertAlmostEqual(square - rounded, exact, delta=0.12 * exact)

    def test_the_default_is_a_catalogue_size_and_says_where_it_comes_from(self):
        oid = self.wall()
        sp = self.spec(oid)
        self.assertEqual(sp["frame_profile"], "stabalux-al5090")
        self.assertEqual((sp["frame_width"], sp["frame_depth"]), (0.05, 0.09))
        self.assertIn("PDF page 99", CW.provenance(sp["frame_profile"]))
        self.assertIn("Gesamtkatalog_Aluminium_NL.pdf", CW.provenance(sp["frame_profile"]))

    def test_catalogue_sizes_are_the_printed_ones(self):
        sr = CW.profile("stabalux-sr6040-2")
        self.assertEqual((sr["depth_mm"], sr["width_mm"], sr["wall_mm"], sr["mass_kg_per_m"], sr["page"]), (40, 60, 2, 3.73, 254))
        al = CW.profile("stabalux-al50120")
        self.assertEqual((al["depth_mm"], al["width_mm"], al["wall_min_mm"], al["mass_kg_per_m"], al["page"]), (120, 50, 2.5, 2.81, 99))
        self.assertEqual([p["width_mm"] for p in CW.profiles_for("Timber")], [50, 60, 80])
        self.assertTrue(all(p["depth_mm"] is None and p["mass_kg_per_m"] is None for p in CW.profiles_for("Timber")))     # the catalogue prints no depth or mass for timber

    def test_every_library_entry_names_its_source_and_page(self):
        lib = CW.library()
        ids = [p["id"] for p in lib["list"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 25)
        for p in lib["list"]:
            self.assertIn(p["material"], CW.FRAME_MATERIALS)
            self.assertIsInstance(p["page"], int)
            self.assertTrue(p["table"])
            self.assertIn(lib["meta"]["sources"][p["material"]], CW.provenance(p["id"]))
        self.assertIn("not approvals", lib["meta"]["note"])

    def test_a_profile_fixes_width_and_depth_and_must_match_the_material(self):
        oid = self.wall(frame_material="Steel", frame_profile="stabalux-sr60140-2")
        self.assertEqual((self.spec(oid)["frame_width"], self.spec(oid)["frame_depth"]), (0.06, 0.14))
        with self.assertRaises(BadRequest):
            self.wall(frame_material="Steel", frame_profile="stabalux-al5090")
        with self.assertRaises(BadRequest):
            self.wall(frame_profile="stabalux-al5090", frame_width=60)
        with self.assertRaises(BadRequest):
            self.wall(frame_profile="no-such-profile")

    def test_typing_a_size_leaves_the_catalogue_and_picking_one_returns(self):
        oid = self.wall()
        self.cmd("model.set_property", id=oid, key="frame_width", value=45)
        self.assertIsNone(self.spec(oid)["frame_profile"])
        self.assertIn("Custom", CW.provenance(self.spec(oid)["frame_profile"]))
        self.cmd("model.set_property", id=oid, key="frame_profile", value="stabalux-al50150")
        sp = self.spec(oid)
        self.assertEqual((sp["frame_width"], sp["frame_depth"], sp["frame_profile"]), (0.05, 0.15, "stabalux-al50150"))
        self.cmd("model.set_property", id=oid, key="frame_material", value="Steel")
        self.assertIsNone(self.spec(oid)["frame_profile"])         # an aluminium profile is not a steel one

    def test_timber_takes_its_width_from_the_catalogue_and_its_depth_from_the_designer(self):
        oid = self.wall(frame_material="Timber", frame_profile="stabalux-h-80", frame_depth=120)
        self.assertEqual((self.spec(oid)["frame_width"], self.spec(oid)["frame_depth"]), (0.08, 0.12))
        q = CW.quantities(self.p, oid)
        self.assertIsNone(q["frame_mass"])                          # no mass per metre is printed for timber: none is invented
        self.assertEqual(self.members(oid)[0]["material"], "Timber")

    def test_mass_comes_from_the_catalogue_mass_per_metre_times_the_member_lengths(self):
        oid = self.wall()
        q = CW.quantities(self.p, oid)
        self.assertAlmostEqual(q["frame_mass"], q["frame_length"] * 2.18)


class Panels(Case):
    def test_the_default_panel_is_glazing_between_the_faces_of_the_members(self):
        oid = self.wall()
        glass = self.members(oid, "glass")
        self.assertEqual(len(glass), 5 * 2)
        first = glass[0]
        xs = [v[0] for v in first["mesh"][0]]
        zs = [v[2] for v in first["mesh"][0]]
        self.assertAlmostEqual(min(xs), 0.05)
        self.assertAlmostEqual(max(xs), 1.2)
        self.assertAlmostEqual(min(zs), 0.05)
        self.assertAlmostEqual(max(zs), 2.1)
        self.assertAlmostEqual(max(v[1] for v in first["mesh"][0]) - min(v[1] for v in first["mesh"][0]), 0.024)

    def test_thickness_and_offset_place_the_panel_across_the_wall(self):
        oid = self.wall(panel_thickness=30, panel_offset=15)
        ys = [v[1] for v in self.members(oid, "glass")[0]["mesh"][0]]
        self.assertAlmostEqual(min(ys), 0.0)
        self.assertAlmostEqual(max(ys), 0.03)
        lo, hi = self.p.model.world_bounds(oid)
        self.assertAlmostEqual(lo[1], -0.045)                   # the frame is centred on the line
        self.assertAlmostEqual(hi[1], 0.045)

    def test_a_panel_that_leaves_the_frame_is_a_finding_not_a_crash(self):
        oid = self.wall(panel_offset=60)
        issues = self.p.check()["issues"]
        self.assertTrue(any("sticks out of the frame" in i["message"] for i in issues if i["ref"] == oid))

    def test_opaque_panels_and_per_field_settings(self):
        oid = self.wall(panel_type="Opaque panel", panel_material="Steel")
        self.assertEqual(len(self.members(oid, "panel")), 10)
        self.assertEqual({q["material"] for q in self.members(oid, "panel")}, {"Steel"})
        self.cmd("model.curtainwall_panel", id=oid, col=2, row=1, field="type", value="Window")
        self.cmd("model.curtainwall_panel", id=oid, col=0, row=0, field="thickness", value=40)
        self.assertEqual(len(self.members(oid, "glass")), 1)
        self.assertEqual(self.members(oid, "glass")[0]["cell"], "2,1")
        thick = next(q for q in self.members(oid, "panel") if q["cell"] == "0,0")
        self.assertAlmostEqual(thick["thickness"], 0.04)
        self.cmd("model.curtainwall_panel", id=oid, col=0, row=0, field="reset", value=None)
        self.assertEqual(self.spec(oid)["panels"].get("0,0"), None)

    def test_removing_a_member_merges_the_two_fields_and_keeps_the_others_settings(self):
        oid = self.wall()
        self.cmd("model.curtainwall_panel", id=oid, col=3, row=1, field="type", value="Opaque panel")
        before = len(CW.cells(self.spec(oid)))
        self.cmd("model.curtainwall_line", id=oid, direction="v", action="remove", index=0)
        self.assertEqual(len(CW.cells(self.spec(oid))), before - 2)           # two rows of fields lose a column each
        merged = next(c for c in CW.cells(self.spec(oid)) if c["key"] == "0,0")
        self.assertAlmostEqual(merged["u1"] - merged["u0"], 2.35)              # 2 x 1200 less the removed mullion's width... 0.05 .. 2.4
        self.assertEqual(self.spec(oid)["panels"], {"2,1": {"type": "Opaque panel"}})   # the field that was 3,1 is 2,1 now: its settings went with it
        listing = CW.lines(self.spec(oid), "v")
        self.assertTrue(listing[0]["removed"])
        self.assertEqual(len(self.axes_mm(oid, "v")), 5)
        self.cmd("model.curtainwall_line", id=oid, direction="v", action="restore", index=0)
        self.assertEqual(len(self.axes_mm(oid, "v")), 6)
        self.assertEqual(self.spec(oid)["panels"], {"3,1": {"type": "Opaque panel"}})

    def test_a_member_is_added_anywhere_a_panel_still_fits(self):
        oid = self.wall()
        self.cmd("model.curtainwall_line", id=oid, direction="h", action="add", at=1000)
        self.assertEqual(self.axes_mm(oid, "h"), [25, 1000, 2125, 2975])
        listing = CW.lines(self.spec(oid), "h")
        self.assertEqual([q["src"] for q in listing], ["added", "rule"])
        self.assertEqual(len(CW.cells(self.spec(oid))), 5 * 3)
        for at in (30, 5990, 1230):
            with self.assertRaises(BadRequest, msg=str(at)):
                self.cmd("model.curtainwall_line", id=oid, direction="v", action="add", at=at)
        self.cmd("model.curtainwall_line", id=oid, direction="h", action="remove", index=0)    # removing an added member deletes it
        self.assertEqual(self.axes_mm(oid, "h"), [25, 2125, 2975])
        self.assertEqual(self.spec(oid)["extra"]["h"], [])

    def test_a_new_spacing_rule_starts_the_member_edits_afresh(self):
        oid = self.wall()
        self.cmd("model.curtainwall_line", id=oid, direction="v", action="remove", index=1)
        self.cmd("model.curtainwall_line", id=oid, direction="v", action="add", at=700)
        self.cmd("model.set_property", id=oid, key="v_value", value=1000)
        self.assertEqual((self.spec(oid)["removed"]["v"], self.spec(oid)["extra"]["v"]), ([], []))

    def test_the_properties_panel_offers_every_member_and_field(self):
        oid = self.wall()
        props = E._properties(self.p, oid)
        keys = {r.get("key") for g in props["groups"] for r in g["rows"]} | {r.get("key") for r in props["top"]}
        for needed in ("v_mode", "v_value", "h_mode", "frame_material", "frame_profile", "frame_width", "frame_depth", "corner_radius", "join", "panel_type", "panel_material",
                       "panel_thickness", "panel_offset", "line:v:0", "line:h:0", "add_line:v", "panel:0:0:type", "panel:4:1:material"):
            self.assertIn(needed, keys)
        self.cmd("model.set_property", id=oid, key="line:v:1", value="removed")
        self.assertTrue(CW.lines(self.spec(oid), "v")[1]["removed"])
        self.cmd("model.set_property", id=oid, key="panel:0:0:type", value="Door")
        self.assertEqual(self.spec(oid)["panels"]["0,0"]["type"], "Door")
        keys = {r.get("key") for g in E._properties(self.p, oid)["groups"] for r in g["rows"]}
        self.assertTrue({"panel:0:0:door_type", "panel:0:0:hinge", "panel:0:0:swing"} <= keys)

    def test_a_failed_edit_leaves_the_wall_as_it_was(self):
        oid = self.wall()
        before = self.volume(oid)
        for key, value in (("frame_width", 3000), ("corner_radius", 40), ("join", "weld"), ("v_value", 0)):
            with self.assertRaises(BadRequest, msg=key):
                self.cmd("model.set_property", id=oid, key=key, value=value)
        self.assertAlmostEqual(self.volume(oid), before)
        self.assertEqual(self.p.check()["issues"], [])


class Doors(Case):
    def door(self, **extra):
        oid = self.wall()
        self.cmd("model.curtainwall_panel", id=oid, col=1, row=0, field="type", value="Door")
        for field, value in extra.items():
            self.cmd("model.curtainwall_panel", id=oid, col=1, row=0, field=field, value=value)
        return oid

    def leaves(self, oid):
        return self.members(oid, "door")

    def symbol(self, oid):
        return self.plan(f=1000.0)[oid]["symbols"][0]

    def test_a_single_flush_door_is_one_leaf_inside_its_field(self):
        oid = self.door()
        (leaf,) = self.leaves(oid)
        xs = [v[0] for v in leaf["mesh"][0]]
        zs = [v[2] for v in leaf["mesh"][0]]
        self.assertAlmostEqual(min(xs), 1.25 + CW.GAP)
        self.assertAlmostEqual(max(xs), 2.4 - CW.GAP)
        self.assertAlmostEqual(min(zs), 0.05 + CW.GAP)
        self.assertAlmostEqual(max(zs), 2.1 - CW.GAP)
        self.assertEqual(len(self.members(oid, "glass")), 9)           # the field is no longer glazing

    def test_the_other_door_types_make_a_pair_or_two_rails(self):
        oid = self.door(door_type="Double Door")
        a, b = self.leaves(oid)
        self.assertAlmostEqual(max(v[0] for v in a["mesh"][0]), (1.25 + 2.4) / 2 - CW.GAP / 2)
        self.assertAlmostEqual(min(v[0] for v in b["mesh"][0]), (1.25 + 2.4) / 2 + CW.GAP / 2)
        oid = self.door(door_type="Sliding Door")
        a, b = self.leaves(oid)
        ya = (min(v[1] for v in a["mesh"][0]) + max(v[1] for v in a["mesh"][0])) / 2
        yb = (min(v[1] for v in b["mesh"][0]) + max(v[1] for v in b["mesh"][0])) / 2
        self.assertAlmostEqual(abs(ya - yb), E.DOOR_LEAF + E.SLIDE_GAP)    # two rails
        self.assertLess(max(v[0] for v in b["mesh"][0]) - min(v[0] for v in a["mesh"][0]), 2.4 - 1.25)        # together they do not exceed the field
        self.assertGreater(max(v[0] for v in a["mesh"][0]) - min(v[0] for v in b["mesh"][0]), 0)             # they overlap

    def test_a_door_stands_on_the_floor_and_needs_room(self):
        oid = self.wall()
        with self.assertRaises(BadRequest) as ctx:
            self.cmd("model.curtainwall_panel", id=oid, col=1, row=1, field="type", value="Door")
            self.cmd("model.set_property", id=oid, key="v_value", value=1200)
        self.assertIn("bottom row", str(ctx.exception))
        narrow = self.wall(v_mode="count", v_value=14)
        with self.assertRaises(BadRequest) as ctx:
            self.cmd("model.curtainwall_panel", id=narrow, col=1, row=0, field="type", value="Door")
        self.assertIn("too small for a door", str(ctx.exception))
        self.assertEqual(len(self.members(narrow, "door")), 0)         # the refused edit changed nothing

    def test_the_plan_arc_starts_at_the_hinge_and_has_the_leafs_width_for_radius(self):
        for hinge, expect_x in (("start", 1.25 + CW.GAP), ("end", 2.4 - CW.GAP)):
            oid = self.door(hinge=hinge)
            sym = self.symbol(oid)
            self.assertAlmostEqual(sym["hinge"][0], expect_x * 1000, places=3)
            self.assertAlmostEqual(sym["radius"], (2.4 - 1.25 - 2 * CW.GAP) * 1000, places=3)
            self.assertAlmostEqual(sym["hinge"][1], E.DOOR_LEAF / 2 * 1000, places=3)     # on the swing-side face of the leaf, not on the frame's face

    def test_swing_side_and_hinge_agree_in_plan_in_elevation_and_in_3d(self):
        cases = {("left", "start"): (1, "south", False, "north", True), ("right", "start"): (-1, "south", True, "north", False)}
        for (swing, hinge), (sign, v1, t1, v2, t2) in cases.items():
            oid = self.door(swing=swing, hinge=hinge)
            sym = self.symbol(oid)
            # plan: the open leaf's far end is on the side the door swings to (left of the wall's direction is +y here)
            self.assertGreater(sign * (sym["leaf_end"][1] - sym["hinge"][1]), 0, msg=f"{swing}")
            # 3D leaf and plan leaf are the same rectangle along the wall
            leaf = self.leaves(oid)[0]
            shape = next(s for s in sym["shapes"] if s["role"] == "leaf")
            self.assertAlmostEqual(min(p[0] for p in shape["p"]), min(v[0] for v in leaf["mesh"][0]) * 1000, places=3)
            self.assertAlmostEqual(max(p[0] for p in shape["p"]), max(v[0] for v in leaf["mesh"][0]) * 1000, places=3)
            # elevation: the symbol is solid when the leaf opens toward the viewer
            south = self.elevation(v1)["items"]
            north = self.elevation(v2)["items"]
            self.assertEqual(next(i for i in south if i["id"] == oid)["opening"]["toward"], t1, msg=f"{swing} seen from the {v1}")
            self.assertEqual(next(i for i in north if i["id"] == oid)["opening"]["toward"], t2, msg=f"{swing} seen from the {v2}")

    def test_a_double_door_draws_both_leaves_hinged_on_the_outer_jambs(self):
        oid = self.door(door_type="Double Door")
        sym = self.symbol(oid)
        self.assertEqual(len(sym["more"]), 1)
        self.assertAlmostEqual(sym["hinge"][0], (1.25 + CW.GAP) * 1000, places=3)
        self.assertAlmostEqual(sym["more"][0]["hinge"][0], (2.4 - CW.GAP) * 1000, places=3)
        el = next(i for i in self.elevation("south")["items"] if i["id"] == oid)["opening"]
        self.assertEqual(len(el["more"]), 1)

    def test_a_sliding_door_shows_its_open_position_track_and_arrow_and_no_swing(self):
        oid = self.door(door_type="Sliding Door")
        sym = self.symbol(oid)
        self.assertNotIn("hinge", sym)
        roles = [s["role"] for s in sym["shapes"]]
        self.assertEqual(roles.count("leaf"), 2)
        for needed in ("open", "track", "direction"):
            self.assertIn(needed, roles)

    def test_the_opening_angle_is_what_the_plan_shows(self):
        oid = self.door(opening_angle=45)
        self.assertAlmostEqual(self.symbol(oid)["opening_angle"], 45)
        with self.assertRaises(BadRequest):
            self.cmd("model.curtainwall_panel", id=oid, col=1, row=0, field="opening_angle", value=1)

    def test_the_door_is_not_drawn_in_a_plan_that_cuts_above_it(self):
        oid = self.door()
        self.assertNotIn("symbols", self.plan(cut_elevation=2.5, top_elevation=4.0)[oid])
        self.assertIn("symbols", self.plan(cut_elevation=1.0)[oid])

    def test_changing_the_hinge_through_properties_moves_the_symbol(self):
        oid = self.door()
        self.cmd("model.set_property", id=oid, key="panel:1:0:hinge", value="end")
        self.assertAlmostEqual(self.symbol(oid)["hinge"][0], (2.4 - CW.GAP) * 1000, places=3)


class Drawings(Case):
    def test_the_plan_cuts_every_member_and_panel_with_its_own_material_and_hatch(self):
        oid = self.wall()
        item = self.plan()[oid]
        self.assertEqual(sorted(set(item["cut_materials"])), ["Aluminium", "Glass"])
        self.assertEqual(item["cut_materials"].count("Aluminium"), 6)                          # the six mullions; no transom reaches the 1.5 m plane
        self.assertEqual(item["cut_materials"].count("Glass"), 5)
        hatches = {m: h for m, h in zip(item["cut_materials"], item["cut_hatches"])}
        self.assertEqual(hatches["Aluminium"]["pattern"], "lines-45")
        self.assertEqual(hatches["Glass"]["pattern"], "none")
        self.assertIsNone(hatches["Aluminium"]["standard"])                                     # SPADRA does not claim NEN 47
        self.assertNotIn("cut_hatches", self.plan(show_hatching=False)[oid])

    def test_a_member_cut_in_plan_has_the_size_of_its_section(self):
        oid = self.wall()
        item = self.plan()[oid]
        loop = next(l for l, k in zip(item["cut_polygons"], item["cut_kinds"]) if k == "frame")
        xs, ys = [p[0] for p in loop], [p[1] for p in loop]
        self.assertAlmostEqual(max(xs) - min(xs), 50, places=3)
        self.assertAlmostEqual(max(ys) - min(ys), 90, places=3)

    def test_a_transom_at_the_cut_plane_is_cut_as_one_band_per_bay(self):
        oid = self.wall(h_value=1480)                                            # a transom axis at 1505 mm: its 50 mm face spans the 1.5 m plane
        item = self.plan()[oid]
        bands = [l for l, k in zip(item["cut_polygons"], item["cut_kinds"]) if k == "frame" and max(p[0] for p in l) - min(p[0] for p in l) > 500]
        self.assertEqual(len(bands), 5)
        self.assertAlmostEqual(max(p[0] for p in bands[0]) - min(p[0] for p in bands[0]), 1150, places=3)       # between the faces of two mullions

    def test_a_plan_that_does_not_reach_the_wall_shows_no_cut(self):
        oid = self.wall()
        item = self.plan(cut_elevation=9.0, top_elevation=10.0, bottom_elevation=8.0, depth_elevation=7.0).get(oid)
        self.assertTrue(item is None or not item.get("cut_polygons"))

    def test_a_section_through_a_mullion_and_a_panel_hatches_each_by_its_material(self):
        oid = self.wall()
        self.p.manifest.setdefault("hatching", {})["Aluminium"] = "cross-45"                    # the studio's own choice wins over the default
        vid = self.cmd("view.add_section", a=[1225, -1000], b=[1225, 1000])["id"]
        pr = D.project_view(self.p, self.p.view(vid), 1000.0)
        item = next(i for i in pr["items"] if i["id"] == oid)
        self.assertEqual(sorted(set(item["cut_materials"])), ["Aluminium"])                       # on the axis of a mullion: its section and the four transom stretches
        self.assertTrue(all(h["pattern"] == "cross-45" for h in item["cut_hatches"]))
        self.assertEqual(len(item["cut_hatches"]), len(item["cut"]))
        vid = self.cmd("view.add_section", a=[700, -1000], b=[700, 1000])["id"]
        item = next(i for i in D.project_view(self.p, self.p.view(vid), 1000.0)["items"] if i["id"] == oid)
        self.assertEqual(item["cut_materials"].count("Glass"), 2)
        self.assertEqual(item["cut_materials"].count("Aluminium"), 3)                              # bottom, middle and top transom

    def test_the_default_aluminium_hatch_is_a_plain_configurable_pattern(self):
        self.assertEqual(D.hatch_pattern(self.p, "Aluminium")["pattern"], "lines-45")
        self.assertIn("NEN 47", D.HATCH_NOTE)

    def test_an_elevation_shows_the_frame_and_every_panel(self):
        oid = self.wall()
        item = next(i for i in self.elevation("south")["items"] if i["id"] == oid)
        self.assertGreater(len(item["tris"]), 20)
        self.assertEqual(item["cut"], [])


class Model(Case):
    def test_a_linear_array_copies_the_wall_in_place_and_undo_removes_the_copies(self):
        oid = self.wall()
        made = self.p.execute("model.array", {"ids": [oid], "mode": "linear", "count": 3, "step": [0, 2000]})["result"]["ids"]
        walls = [i for i in E.element_ids(self.p) if E.category(self.p, i) == "curtainwall"]
        self.assertEqual(len(walls), 3)
        self.assertEqual(sorted(round(E.m(self.meta(i), "start")[1], 6) for i in walls), [0.0, 2.0, 4.0])
        for i in made:                                                                 # each copy's geometry stands where its own line is
            self.assertAlmostEqual(self.p.model.world_transform(i)[7], E.m(self.meta(i), "start")[1], places=6)
        self.p.undo()
        self.assertEqual(len([i for i in E.element_ids(self.p) if E.category(self.p, i) == "curtainwall"]), 1)

    def test_it_is_scheduled_counted_classified_and_quantified(self):
        oid = self.wall()
        row = D.schedule_data(self.p, "curtainwall")["rows"][0]
        self.assertEqual((row["no"], row["mullions"], row["transoms"], row["panels"], row["doors"]), ("CW01", 6, 3, 10, 0))
        self.assertAlmostEqual(row["gross"], 18.0)
        glazing = sum(c["u1"] - c["u0"] for c in CW.cells(self.spec(oid)) if c["row"] == 0) * 2.05 + sum(c["u1"] - c["u0"] for c in CW.cells(self.spec(oid)) if c["row"] == 1) * 0.8
        self.assertAlmostEqual(row["glazing"], glazing, places=2)
        self.assertEqual(row["profile"], "AL5090")
        counts = {r["category"]: r["count"] for r in D.schedule_data(self.p, "count")["rows"]}
        self.assertEqual(counts["Curtain walls"], 1)
        self.assertEqual(BD.entity_of(self.meta(oid)), "IfcCurtainWall")
        self.assertIn("(21.14)", SF.SUGGESTED["curtainwall"])
        self.assertEqual(SF.lookup(1, "(21.14)")["en"], "External walls, non-load bearing, curtain wall")
        rows = {r["id"]: r for r in self.p.quantities()["rows"]}
        layers = {l["material"]: l["volume_m3"] for l in rows[oid]["layers"]}
        self.assertAlmostEqual(sum(layers.values()), rows[oid]["volume_m3"], places=9)
        self.assertEqual(sorted(layers), ["Aluminium", "Glass"])

    def test_3d_colours_the_frame_and_the_glazing_separately(self):
        oid = self.wall()
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)
        runs = obj["layer_colors"]
        self.assertEqual(sum(r["count"] for r in runs), len(obj["faces"]))
        self.assertEqual([r["color"] for r in runs], [E.MATERIAL_COLORS["Aluminium"], E.MATERIAL_COLORS["Glass"]])
        self.p.manifest["material_colors"] = {"Glass": "#112233"}
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == oid)
        self.assertEqual(obj["layer_colors"][1]["color"], "#112233")

    def test_it_moves_and_turns_by_its_parameters(self):
        oid = self.wall()
        self.cmd("model.move", id=oid, dx=1.0, dy=2.0, dz=0.5)
        self.assertEqual([round(c, 6) for c in self.meta(oid)[f"{NS}:start"]], [1.0, 2.0])
        lo, hi = self.p.model.world_bounds(oid)
        self.assertAlmostEqual(lo[2], 0.5 + self.p.manifest["levels"][0]["elevation"])
        self.cmd("model.rotate", ids=[oid], angle=90, centre=[1000, 2000])
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:end"][0], 1.0, places=6)
        self.assertAlmostEqual(self.meta(oid)[f"{NS}:end"][1], 2.0 + 6.0, places=6)
        self.assertAlmostEqual(self.volume(oid), self.parts_volume(oid), places=9)

    def test_a_wall_at_an_angle_has_the_same_solid(self):
        straight = self.wall()
        angled = self.cmd("model.add_curtainwall", start=[0, 1000], end=[4242.640687, 5242.640687], level=self.level, height=H)["id"]
        self.assertAlmostEqual(self.spec(angled)["angle"], math.pi / 4)
        self.assertAlmostEqual(self.volume(angled), self.parts_volume(angled), places=9)
        item = self.plan()[angled]
        self.assertEqual(len(item["polygon"]), 4)
        self.assertAlmostEqual(math.dist(item["polygon"][0], item["polygon"][3]), 90, places=3)

    def test_it_survives_save_and_reopen_and_the_check_is_clean(self):
        oid = self.wall(join="mitre", corner_radius=5, frame_material="Steel", frame_profile="stabalux-sr6090-2")
        self.cmd("model.curtainwall_line", id=oid, direction="v", action="remove", index=1)
        self.cmd("model.curtainwall_panel", id=oid, col=0, row=0, field="type", value="Door")
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "cw.spadra")
            q = Project.open(Path(tmp) / "cw.spadra")
            self.assertEqual(q.check()["issues"], [])
            self.assertEqual(CW.spec(E.meta_of(q, oid)), self.spec(oid))
            self.assertEqual(q.model.tessellate(oid)["faces"], self.p.model.tessellate(oid)["faces"])
            self.assertAlmostEqual(q.model.volume(oid, unit="m"), self.volume(oid), places=9)
            E.regenerate(q, oid)                                                     # rebuilt from its parameters it is the same solid
            self.assertAlmostEqual(q.model.volume(oid, unit="m"), self.volume(oid), places=9)

    def test_undo_and_redo_return_every_kind_of_edit(self):
        oid = self.wall()
        base = self.volume(oid)
        for op, params in (("model.set_property", dict(key="join", value="mitre")), ("model.curtainwall_line", dict(direction="v", action="remove", index=0)),
                           ("model.curtainwall_panel", dict(col=1, row=0, field="type", value="Door")), ("model.set_property", dict(key="v_mode", value="count"))):
            before = (dict(self.meta(oid)), round(self.volume(oid), 9))
            self.cmd(op, id=oid, **params)
            after = (dict(self.meta(oid)), round(self.volume(oid), 9))
            self.p.undo()
            self.assertEqual((dict(self.meta(oid)), round(self.volume(oid), 9)), before, msg=op)
            self.p.redo()
            self.assertEqual((dict(self.meta(oid)), round(self.volume(oid), 9)), after, msg=op)

    def test_deleting_it_and_undoing_brings_it_back(self):
        oid = self.wall()
        self.cmd("model.delete", id=oid)
        self.assertFalse(E.alive(self.p, oid))
        self.p.undo()
        self.assertTrue(E.alive(self.p, oid))

    def test_the_model_check_reports_a_broken_curtain_wall(self):
        oid = self.wall()
        E._attach(self.p, oid, {"frame_width": 5.0})
        found = [i for i in self.p.check()["issues"] if i["ref"] == oid]
        self.assertTrue(found and found[0]["severity"] == "error")


class Creation(Case):
    def test_everything_the_brief_lists_can_be_given_at_creation(self):
        oid = self.wall(v_mode="min", v_value=900, h_mode="count", h_value=2, frame_material="Timber", frame_profile="stabalux-h-60", frame_depth=120, corner_radius=4, join="transoms",
                        panel_type="Opaque panel", panel_material="Timber", panel_thickness=30, panel_offset=-10, name="Gevel")
        sp = self.spec(oid)
        self.assertEqual((sp["v"]["mode"], sp["h"]["mode"], sp["frame_material"], sp["join"], sp["panel_type"], sp["panel_material"]), ("min", "count", "Timber", "transoms", "Opaque panel", "Timber"))
        self.assertAlmostEqual(sp["panel_thickness"], 0.03)
        self.assertAlmostEqual(sp["panel_offset"], -0.01)
        self.assertEqual(self.p.model.get(oid)["name"], "Gevel")

    def test_the_preview_says_what_creation_would_make_without_making_it(self):
        before = len(E.element_ids(self.p))
        pv = self.p.preview_curtainwall(dict(start=[0, 0], end=[W, 0], height=H, v_mode="count", v_value=2, h_mode="centre", h_value=1000))
        self.assertEqual(pv["mullions"], [25, round(25 + 5950 / 3, 3), round(25 + 2 * 5950 / 3, 3), 5975])
        self.assertEqual(pv["panels"], 3 * 3)
        self.assertIn("PDF page 99", pv["profile"])
        self.assertEqual(len(E.element_ids(self.p)), before)
        with self.assertRaises(BadRequest):
            self.p.preview_curtainwall(dict(start=[0, 0], end=[W, 0], height=H, v_mode="centre", v_value=0))

    def test_a_refused_creation_leaves_no_element_behind(self):
        before = len(E.element_ids(self.p))
        for bad in (dict(frame_material="Brass"), dict(join="glue"), dict(height=10), dict(end=[10, 0]), dict(panel_thickness=1), dict(v_mode="centre", v_value=70)):
            with self.assertRaises(BadRequest, msg=str(bad)):
                self.wall(**bad)
        self.assertEqual(len(E.element_ids(self.p)), before)

    def test_the_profile_library_is_served_with_its_provenance(self):
        lib = self.p.curtainwall_profiles()
        self.assertEqual(lib["manufacturer"], "Stabalux")
        self.assertTrue(all("PDF page" in p["provenance"] for p in lib["profiles"]))


if __name__ == "__main__":
    unittest.main()
