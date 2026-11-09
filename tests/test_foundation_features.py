"""Foundations under several connected walls, constrained to what they stand on, with optional formwork, trimmed or merged with the floors they overlap, and reinforced
after they were made (the shading follows). Every figure is checked against arithmetic done by hand, not against the code's own output."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import documentation as D
from applications.SPADRA import elements as E
from applications.SPADRA import foundations as FD
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, NotFound

from .test_foundations import Case

W, H = 6000, 4000                                           # the walls' axes: a 6 m x 4 m rectangle
STRIP = 600                                                 # strip width (mm)


class Rectangle(Case):
    """Four connected walls in a closed rectangle, a level, and helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.walls = [self.wall([0, 0], [W, 0]), self.wall([W, 0], [W, H]), self.wall([W, H], [0, H]), self.wall([0, H], [0, 0])]

    def ring(self, **extra) -> list[str]:
        return self.run_("model.add_foundation", kind="strook", support=self.walls, width=STRIP, height=500, **extra)["ids"]

    def floor(self, points=None, thickness=200) -> str:
        return self.run_("model.add_floor", level=self.l1, points=points or [[0, 0], [W, 0], [W, H], [0, H]], thickness=thickness)["id"]

    def area(self, oid: str) -> float:
        return abs(FD.G.signed_area([tuple(q) for q in self.meta(oid)[f"{NS}:boundary"]]))

    def top(self, oid: str) -> float:
        return self.meta(oid)[f"{NS}:top"]


class SeveralWalls(Rectangle):
    def test_four_connected_walls_get_four_strips_in_one_command_and_the_corners_are_solid_once(self) -> None:
        depth, rev = len(self.p.undo_stack), self.p.revision
        ids = self.ring()
        self.assertEqual(len(ids), 4)
        self.assertEqual((len(self.p.undo_stack), self.p.revision), (depth + 1, rev + 1))
        ring_area = (6.6 * 4.6) - (5.4 * 3.4)                                       # outer rectangle minus inner one
        self.assertAlmostEqual(sum(self.vol(i) for i in ids), ring_area * 0.5, places=9)
        self.p.undo()
        self.assertEqual(E.all_ids(self.p.model, metadata={f"{NS}:category": "foundation"}), [])

    def test_the_single_wall_form_still_works_and_several_walls_through_the_single_call_do_too(self) -> None:
        one = self.found(kind="strook", support=[self.walls[0]], width=STRIP, height=500)
        self.assertAlmostEqual(self.vol(one), 6.0 * 0.6 * 0.5 + 0.0, delta=0.4)    # a free end: not mitred, so only roughly a plain 6 m strip
        res = self.run_("model.add_foundation", kind="strook", support=self.walls[1:3], width=STRIP, height=500)
        self.assertEqual(len(res["ids"]), 2)

    def test_a_support_that_is_no_wall_is_refused_whole(self) -> None:
        before = self.p.revision
        col = self.column([1000, 1000])
        with self.assertRaises((BadRequest, NotFound)):
            self.run_("model.add_foundation", kind="strook", support=[self.walls[0], "no-such-id"], width=STRIP, height=500)
        with self.assertRaises((BadRequest, NotFound)):                              # a column is a fine support, a floor is not
            self.run_("model.add_foundation", kind="strook", support=[self.walls[0], self.floor()], width=STRIP, height=500)
        self.assertEqual(self.p.revision, before + 2)                                # the column and the floor only


class Constraint(Rectangle):
    def test_a_foundation_goes_with_its_wall_sideways_and_up_and_down_by_default(self) -> None:
        w = self.walls[0]
        s = self.ring()[0]
        self.assertAlmostEqual(self.top(s), 0.0, places=9)
        self.run_("model.move", id=w, dx=0, dy=0, dz=-0.3)
        self.assertAlmostEqual(self.top(s), -0.3, places=9)
        self.run_("model.move", id=w, dx=0.0, dy=-0.5, dz=0)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:start"][1], -0.5, places=9)
        self.assertAlmostEqual(self.top(s), -0.3, places=9)
        self.run_("model.set_property", id=w, key="base_offset", value=0)
        self.assertAlmostEqual(self.top(s), 0.0, places=9)
        self.assertEqual(self.p.check()["issues"], [])
        self.p.undo()
        self.assertAlmostEqual(self.top(s), -0.3, places=9)                         # one undo restores wall and foundation together

    def test_it_keeps_a_gap_the_person_chose(self) -> None:
        w = self.walls[0]
        s = self.found(kind="strook", support=[w], width=STRIP, height=500, top=-0.2)       # starts 200 mm below the wall's underside
        self.run_("model.move", id=w, dx=0, dy=0, dz=0.5)
        self.assertAlmostEqual(self.top(s), 0.3, places=9)
        self.assertEqual(self.p.check()["issues"], [])
        self.run_("model.set_property", id=s, key="top_gap", value=-100)
        self.assertAlmostEqual(self.top(s), 0.4, places=9)
        self.run_("model.set_property", id=s, key="top", value=0.1)                      # editing the top changes the gap, which then holds
        self.run_("model.move", id=w, dx=0, dy=0, dz=-0.5)
        self.assertAlmostEqual(self.top(s), -0.4, places=9)

    def test_switching_it_off_frees_the_foundation_and_on_again_snaps_it_back(self) -> None:
        w = self.walls[0]
        s = self.found(kind="strook", support=[w], width=STRIP, height=500)
        with self.assertRaisesRegex(BadRequest, "constrained"):
            self.run_("model.move", id=s, dx=0.1, dy=0, dz=0)
        self.run_("model.set_property", id=s, key="constrain", value="false")
        self.run_("model.move", id=s, dx=0.1, dy=0.2, dz=-0.1)                           # now it can be moved on its own
        self.run_("model.move", id=w, dx=0, dy=1.0, dz=0.4)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:start"][1], 0.2, places=9)           # and the wall no longer takes it along
        self.assertAlmostEqual(self.top(s), -0.1, places=9)
        self.assertTrue(any("below the underside" in m or "above the underside" in m for m in self.problems()))
        self.run_("model.set_property", id=s, key="constrain", value="true")
        self.assertAlmostEqual(self.meta(s)[f"{NS}:start"][1], 1.0, places=9)
        self.assertAlmostEqual(self.top(s), 0.4, places=9)

    def test_a_foundation_on_nothing_cannot_be_constrained(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[2000, 0], width=500, height=400)
        with self.assertRaisesRegex(BadRequest, "nothing to constrain"):
            self.run_("model.set_property", id=s, key="constrain", value="true")

    def test_constrain_off_at_creation_and_a_bad_value_is_refused(self) -> None:
        w = self.walls[0]
        s = self.found(kind="strook", support=[w], width=STRIP, height=500, constrain=False)
        self.run_("model.move", id=w, dx=0, dy=2.0, dz=0)
        self.assertAlmostEqual(self.meta(s)[f"{NS}:start"][1], 0.0, places=9)
        before = self.p.revision
        with self.assertRaises(BadRequest):
            self.found(kind="strook", support=[w], width=STRIP, height=500, constrain="maybe")
        self.assertEqual(self.p.revision, before)

    def test_the_constraint_survives_save_and_reopen(self) -> None:
        w = self.walls[0]
        s = self.ring(top=-0.1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "c.spadra")
            q = Project.open(Path(tmp) / "c.spadra")
            q.execute("model.move", {"id": w, "dx": 0, "dy": 0, "dz": 0.25})
            self.assertAlmostEqual(q.model.get(s)["metadata"][f"{NS}:top"], 0.15, places=9)


class Formwork(Rectangle):
    def test_formwork_is_plates_of_the_given_thickness_against_the_vertical_edges(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0, formwork=50, formwork_material="Timber")
        q = FD.quantities(self.p, s)
        concrete = 4.0 * 0.6 * 0.5
        plates = 2 * (4.0 * 0.05 * 0.5) + 2 * (0.7 * 0.05 * 0.5)                          # two long sides, and two short ends that are 0.6 + 2 x 0.05 wide
        self.assertAlmostEqual(q["concrete_m3"], concrete, places=9)
        self.assertAlmostEqual(q["formwork"]["volume_m3"], plates, places=9)
        self.assertAlmostEqual(self.vol(s), concrete + plates, places=9)                  # the mesh holds both, with nothing counted twice
        self.assertAlmostEqual(q["formwork_m2"], (2 * 4.0 + 2 * 0.6) * 0.5, places=9)
        self.assertEqual((q["formwork"]["material"], q["formwork"]["thickness_m"], q["formwork"]["plates"]), ("Timber", 0.05, 4))

    def test_around_a_closed_ring_the_plates_meet_at_the_corners_and_none_stand_inside_a_joint(self) -> None:
        ids = self.ring(formwork=50)
        t = 0.05
        outside = (6.6 + 2 * t) * (4.6 + 2 * t) - 6.6 * 4.6                                  # outer face
        inside = 5.4 * 3.4 - (5.4 - 2 * t) * (3.4 - 2 * t)                                   # inner face
        want = (outside + inside) * 0.5
        got = sum(FD.quantities(self.p, i)["formwork"]["volume_m3"] for i in ids)
        self.assertAlmostEqual(got, want, places=9)
        self.assertAlmostEqual(sum(self.vol(i) for i in ids), (6.6 * 4.6 - 5.4 * 3.4) * 0.5 + want, places=9)
        face_area = sum(FD.quantities(self.p, i)["formwork_m2"] for i in ids)
        self.assertAlmostEqual(face_area, (2 * (6.6 + 4.6) + 2 * (5.4 + 3.4)) * 0.5, places=9)    # the faces cast against: no end face of a joined strip

    def test_formwork_can_be_added_changed_and_removed_afterwards_and_undone(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        v0 = self.vol(s)
        self.run_("model.set_property", id=s, key="formwork", value=80)
        self.assertGreater(self.vol(s), v0)
        self.run_("model.set_property", id=s, key="formwork_material", value="Steel")
        self.assertEqual(FD.quantities(self.p, s)["formwork"]["material"], "Steel")
        self.run_("model.set_property", id=s, key="formwork", value=40)
        self.assertEqual(FD.quantities(self.p, s)["formwork"]["thickness_m"], 0.04)
        self.run_("model.set_property", id=s, key="formwork", value=0)
        self.assertAlmostEqual(self.vol(s), v0, places=9)
        self.assertIsNone(FD.quantities(self.p, s).get("formwork"))
        self.p.undo()
        self.assertEqual(FD.quantities(self.p, s)["formwork"]["thickness_m"], 0.04)

    def test_bad_formwork_is_refused_and_changes_nothing(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        before = (self.p.revision, self.vol(s))
        for key, value in (("formwork", -5), ("formwork", 900), ("formwork", "thick"), ("formwork_material", "Gold")):
            with self.subTest(key=key, value=value), self.assertRaises(BadRequest):
                self.run_("model.set_property", id=s, key=key, value=value)
        with self.assertRaisesRegex(BadRequest, "Add formwork first"):
            self.run_("model.set_property", id=s, key="formwork_material", value="Steel")
        with self.assertRaises(BadRequest):
            self.found(kind="poer", level=self.l1, point=[0, 0], width=500, length=500, height=400, formwork=20, formwork_material="Gold")
        self.assertEqual((self.p.revision, self.vol(s)), before)

    def test_a_pad_and_a_slab_take_formwork_too(self) -> None:
        pad = self.found(kind="poer", level=self.l1, point=[0, 0], width=1000, length=1000, height=500, top=0, formwork=100)
        self.assertAlmostEqual(FD.quantities(self.p, pad)["formwork"]["volume_m3"], (1.2 * 1.2 - 1.0) * 0.5, places=9)
        slab = self.slab(formwork=50)
        self.assertAlmostEqual(FD.quantities(self.p, slab)["formwork"]["area_m2"], 2 * (6 + 5) * 0.15, places=9)

    def test_the_section_hatches_the_formwork_in_its_own_material_and_the_concrete_in_its_own(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0, formwork=50, formwork_material="EPS")
        vid = self.run_("view.add_section", a=[2000, -1000], b=[2000, 1000])["id"]
        item = next(i for i in D.project_view(self.p, self.p.view(vid), 1000.0)["items"] if i["id"] == s)
        self.assertEqual(sorted(item["cut_material"]), ["Concrete", "EPS", "EPS"])      # the concrete once and a plate on each side

    def test_the_3d_scene_colours_the_formwork_apart_from_the_concrete(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0, formwork=50, formwork_material="Timber")
        obj = next(o for o in self.p.scene_data()["objects"] if o["id"] == s)
        counts = [c["count"] for c in obj["layer_colors"]]
        self.assertEqual(sum(counts), len(obj["faces"]))
        self.assertEqual(len(counts), 2)
        self.assertNotEqual(obj["layer_colors"][0]["color"], obj["layer_colors"][1]["color"])


class Floors(Rectangle):
    def test_by_default_the_floor_is_trimmed_back_to_the_foundation(self) -> None:
        fl = self.floor()
        self.assertAlmostEqual(self.area(fl), 6.0 * 4.0, places=9)
        res = self.run_("model.add_foundation", kind="strook", support=self.walls, width=STRIP, height=500)
        self.assertEqual(res["floors"]["adjusted"], [fl])
        self.assertAlmostEqual(self.area(fl), 5.4 * 3.4, places=7)                       # the part under the strips is no longer floor
        self.assertAlmostEqual(self.vol(fl), 5.4 * 3.4 * 0.2, places=7)
        self.assertEqual(self.p.check()["issues"], [])

    def test_deleting_the_foundation_or_undoing_gives_the_floor_its_outline_back(self) -> None:
        fl = self.floor()
        ids = self.ring()
        self.assertAlmostEqual(self.area(fl), 5.4 * 3.4, places=7)
        self.p.undo()
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)
        self.p.redo()
        self.assertAlmostEqual(self.area(fl), 5.4 * 3.4, places=7)
        for i in ids:
            self.run_("model.delete", id=i)
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)
        self.assertIsNone(self.meta(fl).get(f"{NS}:fd_free"))

    def test_changing_a_strip_or_moving_its_wall_re_trims_the_floor_and_moving_back_restores_it(self) -> None:
        fl = self.floor()
        ids = self.ring()
        self.run_("model.set_property", id=ids[0], key="width", value=1000)               # the south strip is wider: 0.5 m in from its axis instead of 0.3
        self.assertAlmostEqual(min(q[1] for q in self.meta(fl)[f"{NS}:boundary"]), 0.5, places=6)
        self.run_("model.move", id=self.walls[0], dx=0, dy=0.5, dz=0)                      # the south wall moves in by 0.5 m: its strip now reaches y = 1.0
        self.assertAlmostEqual(self.area(fl), 5.4 * 2.7, places=9)                          # 5.4 m between the side strips, from y = 1.0 up to the north strip's edge at 3.7
        self.run_("model.move", id=self.walls[0], dx=0, dy=-0.5, dz=0)
        self.assertAlmostEqual(min(q[1] for q in self.meta(fl)[f"{NS}:boundary"]), 0.5, places=6)
        self.assertAlmostEqual(self.area(fl), 5.4 * 3.2, places=9)

    def test_merge_runs_the_floor_over_the_foundation_and_lowers_the_foundation_to_the_floors_underside(self) -> None:
        fl = self.floor()
        res = self.run_("model.add_foundation", kind="strook", support=self.walls, width=STRIP, height=500, floors="merge")
        ids = res["ids"]
        self.assertEqual(res["floors"]["merged"], [fl])
        self.assertAlmostEqual(self.area(fl), 6.6 * 4.6, places=7)
        for i in ids:
            self.assertAlmostEqual(self.top(i), -0.2, places=9)                           # at the floor's underside
            self.assertAlmostEqual(self.meta(i)[f"{NS}:height"], 0.3, places=9)           # its underside has not moved: 200 + 300 = 500
        total = self.vol(fl) + sum(self.vol(i) for i in ids)
        self.assertAlmostEqual(total, 6.6 * 4.6 * 0.2 + (6.6 * 4.6 - 5.4 * 3.4) * 0.3, places=7)      # nothing counted twice
        self.run_("model.move", id=self.walls[0], dx=0, dy=0, dz=-0.1)
        self.assertAlmostEqual(self.top(ids[0]), -0.3, places=9)                          # still constrained to the wall, now 100 mm lower
        self.assertEqual(self.p.check()["issues"], [])

    def test_keep_leaves_both_alone(self) -> None:
        fl = self.floor()
        res = self.run_("model.add_foundation", kind="strook", support=self.walls, width=STRIP, height=500, floors="keep")
        self.assertEqual(res["floors"], {"adjusted": [], "merged": [], "restored": [], "unresolved": []})
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)

    def test_the_policy_can_be_changed_afterwards(self) -> None:
        fl = self.floor()
        ids = self.ring(floors="keep")
        for i in ids:
            self.run_("model.set_property", id=i, key="floors", value="adjust")
        self.assertAlmostEqual(self.area(fl), 5.4 * 3.4, places=7)
        for i in ids:
            self.run_("model.set_property", id=i, key="floors", value="keep")
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)

    def test_a_floor_above_the_foundation_is_left_alone(self) -> None:
        fl = self.floor()
        self.ring(top=-0.2)                                                                # the top of the strips is the underside of the 200 mm floor: they only touch
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)

    def test_a_floor_that_the_trim_would_split_is_left_alone_and_the_check_says_why(self) -> None:
        band = self.floor(points=[[-1000, 1500], [7000, 1500], [7000, 2000], [-1000, 2000]])        # crosses the whole foundation: trimming it would give two floors
        self.ring()
        self.assertAlmostEqual(self.area(band), 8.0 * 0.5, places=9)
        self.assertTrue(any("could not be adjusted" in m and "split in two" in m for m in self.problems()), self.problems())

    def test_a_floor_on_another_level_is_not_touched(self) -> None:
        # a floor a metre higher than the foundation's top never reaches into it
        fl = self.floor()
        self.p.execute("model.set_property", {"id": fl, "key": "offset", "value": 1000})
        self.ring()
        self.assertAlmostEqual(self.area(fl), 24.0, places=9)


class Reinforcement(Rectangle):
    def test_reinforcing_after_creation_changes_the_shading_and_removing_it_changes_it_back(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        vid = self.run_("view.add_section", a=[2000, -1000], b=[2000, 1000])["id"]
        cut = lambda: next(i for i in D.project_view(self.p, self.p.view(vid), 1000.0)["items"] if i["id"] == s)["cut_material"]            # noqa: E731
        colour = lambda: next(o for o in self.p.scene_data()["objects"] if o["id"] == s)["color"]                                              # noqa: E731
        self.assertEqual(cut(), ["Concrete"])
        plain = colour()
        self.run_("model.set_foundation_data", id=s, bars=[{"diameter": 12, "role": "long", "count": 4, "face": "bottom"}, {"diameter": 8, "role": "link", "spacing": 200, "face": "both"}],
                  concrete_class="C25/30", steel="B500B")
        self.assertEqual(cut(), ["Reinforced concrete"])
        self.assertNotEqual(colour(), plain)
        self.assertEqual(FD.quantities(self.p, s)["display_material"], "Reinforced concrete")
        self.assertEqual(self.meta(s)[f"{NS}:material"], "Concrete")                      # the material itself is unchanged
        self.p.undo()
        self.assertEqual(cut(), ["Concrete"])
        self.p.redo()
        self.run_("model.set_foundation_data", id=s, bars=[])
        self.assertEqual(cut(), ["Concrete"])
        self.assertEqual(colour(), plain)

    def test_the_properties_panel_offers_to_reinforce_and_shows_what_it_is_shown_as(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        props = E.properties(self.p, s)
        group = next(g for g in props["groups"] if g["title"] == "Concrete and reinforcement")
        self.assertEqual(group["action"], {"label": "Reinforce…", "dialog": "reinforce"})
        self.assertIn(("Shown as", "Concrete"), [(r["label"], r["value"]) for r in group["rows"]])

    def test_reinforcing_a_raft_and_a_pad_after_creation(self) -> None:
        slab = self.slab()
        self.run_("model.set_foundation_data", id=slab, bars=[{"diameter": 10, "role": "x", "spacing": 200, "face": "bottom"}, {"diameter": 10, "role": "y", "spacing": 200, "face": "bottom"}])
        self.assertEqual(FD.loop_materials(self.p, self.meta(slab), [[(0, -0.07), (1, -0.07), (1, -0.03), (0, -0.03)]]), ["Reinforced concrete"])
        with self.assertRaises(BadRequest):
            self.run_("model.set_foundation_data", id=slab, bars=[{"diameter": 10, "role": "long", "spacing": 200, "face": "bottom"}])     # a slab has no 'long' bars

    def test_formwork_and_floor_policy_can_also_be_set_through_the_edit_command(self) -> None:
        s = self.found(kind="strook", level=self.l1, start=[0, 0], end=[4000, 0], width=600, height=500, top=0)
        self.run_("model.set_foundation_data", id=s, formwork=60, formwork_material="Plywood", floors="keep")
        self.assertEqual((self.meta(s)[f"{NS}:formwork"], self.meta(s)[f"{NS}:formwork_material"], self.meta(s)[f"{NS}:floors"]), (0.06, "Plywood", "keep"))


if __name__ == "__main__":
    unittest.main()
