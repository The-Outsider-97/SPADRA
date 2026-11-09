"""The report's worked examples (sections 4.4-4.6) as acceptance tests: door, window and beam families, and a window's generated lights.

What the report asks of them: the stated numbers come out; parameters are exercised at their minimum, typical and maximum; a combination that would leave
a negative or sliver region is refused with nothing changed; one undo undoes one edit; and for the window, no duplicate piece ids, no negative glazing
regions and an unchanged centre offset when the width changes. Nothing here is a code-compliance or structural claim.
"""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import families as FM
from applications.SPADRA import procedural as PG
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

MM = 0.001


class Base(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Worked")
        self.l0 = self.p.manifest["levels"][0]["id"]

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    def family(self, template):
        return self.x("family.from_template", template=template)["id"]

    def place(self, fid, point=(0, 0), **kw):
        return self.x("family.place", family=fid, level=self.l0, point=list(point), **kw)["id"]

    def values(self, oid):
        return FM.values(self.p, oid)

    def size(self, oid):
        return list(self.p.model.get(oid)["geometry"]["size"])


class DoorFamily(Base):
    def test_the_reports_numbers(self):
        v = self.values(self.place(self.family("door")))
        self.assertAlmostEqual(v["clear_width"], 830 * MM)             # Width - 2 * FrameThickness
        self.assertAlmostEqual(v["clearance_budget"], 820 * MM)        # ClearWidth - NominalClearance
        self.assertIs(v["show_threshold"], True)                       # Width > 800 mm

    def test_minimum_typical_and_maximum_types(self):
        fid = self.family("door")
        self.x("family.type_define", id=fid, name="Min", values={"width": 600, "height": 1800, "frame_thickness": 80})
        self.x("family.type_define", id=fid, name="Typical", values={})
        self.x("family.type_define", id=fid, name="Max", values={"width": 1800, "height": 2600, "frame_thickness": 20})
        got = {}
        for name in ("Min", "Typical", "Max"):
            oid = self.place(fid, point=(len(got) * 3000, 0), type=name)
            got[name] = self.values(oid)
        self.assertAlmostEqual(got["Min"]["clear_width"], 440 * MM)
        self.assertIs(got["Min"]["show_threshold"], False)             # 600 mm is not wider than 800 mm
        self.assertAlmostEqual(got["Typical"]["clear_width"], 830 * MM)
        self.assertAlmostEqual(got["Max"]["clear_width"], 1760 * MM)
        self.assertIs(got["Max"]["show_threshold"], True)
        for name, v in got.items():
            self.assertGreater(v["clear_width"], 0, name)
            self.assertLess(2 * v["frame_thickness"], v["width"], name)   # the report's rule: frame under half the width

    def test_a_value_outside_its_bounds_is_refused_and_nothing_changes(self):
        fid = self.family("door")
        oid = self.place(fid)
        before, revision = self.size(oid), self.p.revision
        with self.assertRaises(BadRequest):
            self.x("family.type_define", id=fid, name="TooNarrow", values={"width": 500})
        with self.assertRaises(BadRequest):
            self.x("family.type_define", id=fid, name="BadFrame", values={"frame_thickness": 400})
        self.assertEqual(self.p.revision, revision)
        self.assertEqual(self.size(oid), before)

    def test_changing_the_type_regenerates_the_instance_and_one_undo_restores_it(self):
        fid = self.family("door")
        self.x("family.type_define", id=fid, name="D900", values={})
        oid = self.place(fid, type="D900")
        before = (self.size(oid), self.values(oid))
        self.x("family.type_update", id=fid, name="D900", values={"width": 1000})
        self.assertAlmostEqual(self.size(oid)[0], 1.0)
        self.assertAlmostEqual(self.values(oid)["clear_width"], 930 * MM)
        self.p.undo()
        self.assertEqual((self.size(oid), self.values(oid)), before)
        self.p.redo()
        self.assertAlmostEqual(self.size(oid)[0], 1.0)


class WindowFamily(Base):
    def test_the_reports_numbers(self):
        v = self.values(self.place(self.family("window")))
        self.assertAlmostEqual(v["clear_span"], 1380 * MM)
        self.assertAlmostEqual(v["light_width"], (1380 - 2 * 40) / 3 * MM)      # (1380 - 80) / 3 = 433.33 mm
        self.assertIs(v["show_muntin"], True)
        self.assertEqual(v["panels"], 3)

    def test_lights_add_up_to_the_clear_span_for_every_panel_count(self):
        fid = self.family("window")
        for n in range(1, 9):
            self.x("family.type_define", id=fid, name=f"N{n}", values={"panels": n})
            v = self.values(self.place(fid, point=(n * 4000, 0), type=f"N{n}"))
            self.assertAlmostEqual(n * v["light_width"] + (n - 1) * v["mullion"], v["clear_span"], places=9)
            self.assertGreater(v["light_width"], 0)
            self.assertIs(v["show_muntin"], n > 1)

    def test_minimum_and_maximum_types(self):
        fid = self.family("window")
        self.x("family.type_define", id=fid, name="Min", values={"width": 400, "height": 400, "frame_width": 20, "panels": 1, "mullion": 10})
        self.x("family.type_define", id=fid, name="Max", values={"width": 4000, "height": 3000, "frame_width": 150, "panels": 8, "mullion": 120})
        low, high = self.values(self.place(fid, type="Min")), self.values(self.place(fid, point=(6000, 0), type="Max"))
        self.assertAlmostEqual(low["light_width"], 360 * MM)
        self.assertIs(low["show_muntin"], False)
        self.assertAlmostEqual(high["light_width"], (3700 - 7 * 120) / 8 * MM)

    def test_a_combination_with_negative_glazing_is_refused_before_anything_changes(self):
        fid = self.family("window")
        oid = self.place(fid)
        revision, before = self.p.revision, self.values(oid)
        with self.assertRaises(BadRequest):       # clear span 100 mm, eight lights and seven 120 mm mullions: negative glazing
            self.x("family.type_define", id=fid, name="Impossible", values={"width": 400, "frame_width": 150, "panels": 8, "mullion": 120})
        self.assertEqual(self.p.revision, revision)
        self.assertEqual(self.values(oid), before)

    def test_changing_the_width_keeps_the_instance_where_it_is(self):
        fid = self.family("window")
        self.x("family.type_define", id=fid, name="W", values={})
        oid = self.place(fid, point=(2500, 1200), type="W")
        centre = list(self.p.model.get(oid)["transform"][3:12:4])
        self.x("family.type_update", id=fid, name="W", values={"width": 2400})
        self.assertAlmostEqual(self.size(oid)[0], 2.4)
        self.assertEqual(list(self.p.model.get(oid)["transform"][3:12:4])[:2], centre[:2])     # same point; the offset is not disturbed


class BeamFamily(Base):
    def test_the_reports_numbers(self):
        v = self.values(self.place(self.family("beam")))
        self.assertAlmostEqual(v["area"], 0.080, places=9)
        self.assertAlmostEqual(v["second_moment"], 0.0010667, places=7)
        self.assertAlmostEqual(v["volume"], 0.320, places=9)

    def test_section_properties_scale_as_the_formulas_say(self):
        fid = self.family("beam")
        self.x("family.type_define", id=fid, name="Deep", values={"height": 800})
        self.x("family.type_define", id=fid, name="Wide", values={"width": 400})
        oid0 = self.place(fid, point=(0, 0))
        base = self.values(oid0)
        deep = self.values(self.place(fid, point=(5000, 0), type="Deep"))
        wide = self.values(self.place(fid, point=(10000, 0), type="Wide"))
        self.assertAlmostEqual(deep["second_moment"] / base["second_moment"], 8.0)      # I = b h^3 / 12
        self.assertAlmostEqual(deep["area"] / base["area"], 2.0)
        self.assertAlmostEqual(wide["second_moment"] / base["second_moment"], 2.0)

    def test_minimum_and_maximum_sections(self):
        fid = self.family("beam")
        self.x("family.type_define", id=fid, name="Min", values={"width": 50, "height": 50, "depth": 500})
        self.x("family.type_define", id=fid, name="Max", values={"width": 1500, "height": 2500, "depth": 30000})
        low, high = self.values(self.place(fid, type="Min")), self.values(self.place(fid, point=(40000, 0), type="Max"))
        self.assertAlmostEqual(low["second_moment"], 0.05 * 0.05 ** 3 / 12, places=12)
        self.assertAlmostEqual(high["volume"], 1.5 * 2.5 * 30.0, places=6)


class WindowLights(Base):
    def setUp(self):
        super().setUp()
        self.wall = self.x("model.add_wall", level=self.l0, start=[0, 0], end=[8000, 0], thickness=200, height=3.0)["id"]
        self.win = self.x("model.add_window", wall=self.wall, offset=3.0, width=1500, height=1200, sill=900)["id"]

    def lights(self, panels=3, **kw):
        return self.x("procedural.lights", opening=self.win, panels=panels, **kw)

    def u_extent(self, oid):
        """(u0, u1, v0, v1) along the wall and up, read from the stored geometry (the wall runs along +x from the origin)."""
        obj = self.p.model.get(oid)
        t, size = obj["transform"], obj["geometry"]["size"]
        return (t[3] - size[0] / 2, t[3] + size[0] / 2, t[11] - size[2] / 2, t[11] + size[2] / 2)

    def test_the_reports_numbers_and_layout(self):
        g = self.lights(3)
        self.assertEqual(g["report"]["lights"], 3)
        self.assertEqual(g["report"]["mullions"], 2)
        self.assertAlmostEqual(g["report"]["light_width"], 1300 / 3 * MM)
        glass = sorted((self.u_extent(o) for k, o in g["members"].items() if k.startswith("g:")))
        self.assertEqual(len(glass), 3)
        x0, x1, z0, z1 = E.opening_span(E.meta_of(self.p, self.win))
        self.assertAlmostEqual(glass[0][0], x0 + 0.06)                           # first light starts one frame width inside the window
        self.assertAlmostEqual(glass[-1][1], x1 - 0.06)
        for a, b in zip(glass, glass[1:]):
            self.assertAlmostEqual(b[0] - a[1], 0.04)                            # the mullion between two lights
        for u0, u1, v0, v1 in glass:
            self.assertGreater(u1 - u0, 0)
            self.assertGreater(v1 - v0, 0)
            self.assertAlmostEqual(v0, z0 + 0.06)
            self.assertAlmostEqual(v1, z1 - 0.06)

    def test_piece_ids_are_unique_and_stay_the_same_across_regeneration(self):
        g = self.lights(4)
        ids = list(g["members"].values())
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 4 + 3)
        self.x("procedural.update", id=g["id"], mullion=60)
        self.assertEqual(dict(PG.group(self.p, g["id"])["members"]), g["members"])           # same keys, same element ids
        self.x("procedural.regenerate", id=g["id"])
        self.assertEqual(dict(PG.group(self.p, g["id"])["members"]), g["members"])

    def test_one_light_has_no_mullions(self):
        g = self.lights(1)
        self.assertEqual(g["report"]["mullions"], 0)
        self.assertEqual(sorted(g["members"]), ["g:0"])

    def test_no_negative_or_sliver_glazing_for_any_count_or_width(self):
        refused = accepted = 0
        for width in (400, 700, 1200, 2400):
            self.x("model.set_property", id=self.win, key="width", value=width)
            for n in range(1, 13):
                for mull in (10, 40, 120):
                    try:
                        values = PG.lights_values(E.opening_span(E.meta_of(self.p, self.win)), {"frame": 0.06, "mullion": mull * MM, "panels": n})
                    except BadRequest:
                        refused += 1
                        continue
                    accepted += 1
                    self.assertGreaterEqual(values["light_width"], PG.MIN_LIGHT - 1e-12)
                    self.assertAlmostEqual(n * values["light_width"] + (n - 1) * mull * MM, values["clear_span"], places=9)
        self.assertGreater(refused, 0)           # the grid does contain impossible combinations, and they were refused rather than drawn
        self.assertGreater(accepted, 0)

    def test_changing_the_width_keeps_the_centre_offset_and_the_piece_ids(self):
        g = self.lights(3)
        centre = lambda: E.m(E.meta_of(self.p, self.win), "offset")                          # noqa: E731
        self.assertAlmostEqual(centre(), 3.0)
        self.x("model.set_property", id=self.win, key="width", value=2400)
        self.assertAlmostEqual(centre(), 3.0)                                                 # the window's offset did not move
        self.assertEqual(dict(PG.group(self.p, g["id"])["members"]), g["members"])           # no piece was recreated or duplicated
        glass = [self.u_extent(o) for k, o in g["members"].items() if k.startswith("g:")]
        self.assertAlmostEqual(sum((a + b) / 2 for a, b, _, _ in glass) / 3, 3.0)             # the lights are still centred on it
        self.assertAlmostEqual(PG.group(self.p, g["id"])["report"]["light_width"], (2400 - 120 - 80) / 3 * MM)

    def test_a_width_that_would_leave_negative_glazing_is_refused_and_one_undo_undoes_one_edit(self):
        g = self.lights(3)
        before = dict(PG.group(self.p, g["id"])["report"])
        revision = self.p.revision
        with self.assertRaises(BadRequest):
            self.x("model.set_property", id=self.win, key="width", value=150)                # clear span 30 mm for three lights and two mullions
        self.assertEqual(self.p.revision, revision)
        self.assertEqual(E.m(E.meta_of(self.p, self.win), "width"), 1.5)
        self.assertEqual(PG.group(self.p, g["id"])["report"], before)
        self.x("model.set_property", id=self.win, key="width", value=1800)
        self.assertAlmostEqual(PG.group(self.p, g["id"])["report"]["light_width"], (1800 - 120 - 80) / 3 * MM)
        self.p.undo()                                                                         # window and lights come back together
        self.assertEqual(E.m(E.meta_of(self.p, self.win), "width"), 1.5)
        self.assertEqual(PG.group(self.p, g["id"])["report"], before)

    def test_the_lights_follow_a_host_change(self):
        g = self.lights(3)
        first = {k: self.u_extent(o) for k, o in g["members"].items()}
        self.x("model.move", id=self.wall, dx=0.0, dy=0.5, dz=0.0)
        obj = self.p.model.get(g["members"]["g:0"])
        self.assertAlmostEqual(obj["transform"][7], 0.5)                                      # moved with the wall, along y
        self.assertEqual({k: self.u_extent(o) for k, o in g["members"].items()}, first)       # and not along it
        self.p.undo()
        self.assertAlmostEqual(self.p.model.get(g["members"]["g:0"])["transform"][7], 0.0)

    def test_deleting_the_window_marks_the_group_and_keeps_its_pieces(self):
        g = self.lights(3)
        self.x("model.delete", id=self.win)
        d = [row for row in PG.describe(self.p) if row["id"] == g["id"]][0]
        self.assertEqual(d["host"], "missing")
        self.assertEqual(d["members"], 5)

    def test_a_door_cannot_have_lights_and_a_window_only_one_group(self):
        door = self.x("model.add_door", wall=self.wall, offset=6.0, width=900)["id"]
        with self.assertRaises(BadRequest):
            self.x("procedural.lights", opening=door)
        self.lights(2)
        with self.assertRaises(BadRequest):
            self.lights(3)

    def test_unknown_parameters_and_bad_counts_are_refused(self):
        with self.assertRaises(BadRequest):
            self.x("procedural.lights", opening=self.win, panes=3)
        with self.assertRaises(BadRequest):
            self.x("procedural.lights", opening=self.win, panels=2.5)
        with self.assertRaises(BadRequest):
            self.x("procedural.lights", opening=self.win, panels=13)

    def test_the_model_check_does_not_flag_lights_standing_in_their_own_opening(self):
        self.lights(3)
        messages = [i["message"] for i in self.p.check()["issues"]]
        self.assertEqual([m for m in messages if "Window Light" in m or "Window Mullion" in m], [])

    def test_a_light_that_really_intrudes_into_another_wall_is_still_reported(self):
        self.lights(3)
        other = self.x("model.add_wall", level=self.l0, start=[0, 0.1], end=[8000, 0.1], thickness=200, height=3.0)["id"]
        messages = [i["message"] for i in self.p.check()["issues"]]
        self.assertTrue(any("Window Light" in m for m in messages), (other, messages))

    def test_save_and_reopen_keeps_the_group(self):
        g = self.lights(3)
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "w.spadra"
            self.p.save(path)
            q = Project.open(path)
            self.assertEqual(dict(PG.group(q, g["id"])["members"]), g["members"])
            self.assertEqual(PG.group(q, g["id"])["report"]["lights"], 3)


if __name__ == "__main__":
    unittest.main()
