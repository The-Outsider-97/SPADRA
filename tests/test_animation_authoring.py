"""Authoring commands (set a key, pose, move, presets, markers, settings). The pure bookkeeping in ``animation_authoring`` is tested without
the agent; everything that is committed or evaluated runs against the real SLAI Animation Agent and is skipped where it is absent."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import animation_authoring as AA
from applications.SPADRA import animation_bridge as AB
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, NotFound

AVAILABLE = AB.BRIDGE.available
SKIP = f"the SLAI Animation Agent is not importable here ({AB.BRIDGE.reason})"
OID = "11111111-1111-4111-8111-111111111111"


class Bookkeeping(unittest.TestCase):
    """No agent involved: how keys, tangents and markers are arranged in the spec."""

    def spec(self):
        return AA.clean_spec(None, 4.0)

    def test_a_new_track_keyed_after_zero_also_keys_the_starting_value(self):
        s = self.spec()
        notes = AA.set_key(s, OID, "translation", 2.0, [0, 0, 2], fps=24)
        keys = s["tracks"][0]["keyframes"]
        self.assertEqual([k["time"] for k in keys], [0.0, 2.0])
        self.assertEqual(keys[0]["value"], [0.0, 0.0, 0.0])
        self.assertTrue(notes)

    def test_a_new_track_keyed_at_zero_adds_no_extra_key(self):
        s = self.spec()
        self.assertEqual(AA.set_key(s, OID, "translation", 0.0, [1, 0, 0], fps=24), [])
        self.assertEqual(len(s["tracks"][0]["keyframes"]), 1)

    def test_times_snap_to_whole_frames_and_stay_inside_the_animation(self):
        self.assertEqual(AA.frame_time(1.01, 24, 4.0), round(round(1.01 * 24) / 24, 6))
        self.assertEqual(AA.frame_time(99, 24, 4.0), 4.0)
        self.assertEqual(AA.frame_time(-3, 24, 4.0), 0.0)

    def test_a_key_at_an_occupied_frame_is_replaced_not_duplicated(self):
        s = self.spec()
        AA.set_key(s, OID, "translation", 2.0, [0, 0, 2], fps=24)
        AA.set_key(s, OID, "translation", 2.0, [0, 0, 5], fps=24)
        keys = s["tracks"][0]["keyframes"]
        self.assertEqual([k["value"] for k in keys if k["time"] == 2.0], [[0, 0, 5]])
        self.assertEqual(len(keys), 2)

    def test_keys_stay_in_time_order(self):
        s = self.spec()
        for t in (3.0, 1.0, 2.0):
            AA.set_key(s, OID, "turn", t, math.radians(t * 10), fps=24)
        times = [k["time"] for k in s["tracks"][0]["keyframes"]]
        self.assertEqual(times, sorted(times))

    def test_turns_use_the_vertical_axis_and_are_stored_in_radians(self):
        s = self.spec()
        AA.set_key(s, OID, "turn", 4.0, AA.spec_value("turn", 90), fps=24)
        track = s["tracks"][0]
        self.assertEqual(track["property"], "rotation_angle")
        self.assertEqual(track["axis"], [0.0, 0.0, 1.0])
        self.assertAlmostEqual(track["keyframes"][-1]["value"], math.pi / 2)

    def test_smooth_tracks_ease_at_the_ends_and_follow_the_neighbours_between(self):
        s = self.spec()
        for t, v in ((0.0, 0.0), (1.0, 1.0), (2.0, 4.0), (3.0, 4.0)):
            AA.set_key(s, OID, "turn", t, v, fps=24)
        keys = s["tracks"][0]["keyframes"]
        self.assertEqual(keys[0]["out_tangent"], 0.0)
        self.assertEqual(keys[-1]["in_tangent"], 0.0)
        self.assertAlmostEqual(keys[1]["out_tangent"], (4.0 - 0.0) / 2.0)          # Catmull-Rom: slope between the neighbours, per second
        self.assertAlmostEqual(keys[2]["in_tangent"], (4.0 - 1.0) / 2.0)

    def test_switching_to_linear_or_step_removes_the_tangents(self):
        s = self.spec()
        AA.set_key(s, OID, "turn", 2.0, 1.0, fps=24)
        AA.set_interpolation(s, OID, "turn", "linear")
        self.assertTrue(all("in_tangent" not in k and "out_tangent" not in k for k in s["tracks"][0]["keyframes"]))
        AA.set_interpolation(s, OID, "turn", "smooth")
        self.assertTrue(all("in_tangent" in k for k in s["tracks"][0]["keyframes"]))

    def test_visibility_is_always_a_step_and_refuses_other_interpolation(self):
        s = self.spec()
        AA.set_key(s, OID, "visibility", 2.0, False, fps=24)
        self.assertEqual(s["tracks"][0]["interpolation"], "step")
        with self.assertRaises(BadRequest):
            AA.set_interpolation(s, OID, "visibility", "smooth")

    def test_deleting_the_last_key_removes_the_track(self):
        s = self.spec()
        AA.set_key(s, OID, "translation", 0.0, [1, 0, 0], fps=24)
        self.assertTrue(AA.delete_key(s, OID, "translation", 0.0, fps=24))
        self.assertEqual(s["tracks"], [])
        self.assertFalse(AA.delete_key(s, OID, "translation", 0.0, fps=24))

    def test_moving_a_key_onto_another_is_refused_and_changes_nothing(self):
        s = self.spec()
        AA.set_key(s, OID, "translation", 1.0, [1, 0, 0], fps=24)
        AA.set_key(s, OID, "translation", 2.0, [2, 0, 0], fps=24)
        before = [dict(k) for k in s["tracks"][0]["keyframes"]]
        with self.assertRaisesRegex(BadRequest, "already a key"):
            AA.move_key(s, OID, "translation", 1.0, 2.0, fps=24)
        self.assertEqual(s["tracks"][0]["keyframes"], before)
        self.assertEqual(AA.move_key(s, OID, "translation", 1.0, 3.0, fps=24), 3.0)

    def test_values_are_validated(self):
        for prop, bad in (("translation", [1, 2]), ("translation", [1, 2, float("nan")]), ("translation", "x"), ("scale", [0, 1, 1]),
                          ("turn", True), ("turn", float("inf")), ("visibility", 1), ("visibility", "yes")):
            with self.assertRaises(BadRequest, msg=f"{prop} {bad!r}"):
                AA.spec_value(prop, bad)
        with self.assertRaises(BadRequest):
            AA.spec_property("colour")

    def test_markers_get_canonical_uuids_and_can_be_deleted(self):
        s = self.spec()
        mid = AA.add_marker(s, 1.0, "Door opens", fps=24)
        self.assertEqual(len(mid), 36)
        AA.delete_marker(s, mid)
        self.assertNotIn("events", s)
        with self.assertRaises(BadRequest):
            AA.delete_marker(s, mid)

    def test_presets_make_the_documented_keys(self):
        s = self.spec()
        AA.apply_preset(s, OID, "slide", {"by": [3, 0, 0]}, fps=24, start=0.0, end=2.0)
        self.assertEqual([k["value"] for k in s["tracks"][0]["keyframes"]], [[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
        AA.apply_preset(s, OID, "disappear", {}, fps=24, start=1.0, end=1.5)
        vis = next(t for t in s["tracks"] if t["property"] == "visibility")
        self.assertEqual([(k["time"], k["value"]) for k in vis["keyframes"]], [(0.0, True), (1.0, True), (1.5, False)])
        for kind, params in (("slide", {}), ("spin", {"degrees": 0}), ("grow", {"factor": 1}), ("grow", {"factor": -2}), ("fly", {})):
            with self.assertRaises(BadRequest, msg=kind):
                AA.apply_preset(self.spec(), OID, kind, params, fps=24, start=0.0, end=1.0)
        with self.assertRaises(BadRequest):
            AA.apply_preset(self.spec(), OID, "spin", {}, fps=24, start=2.0, end=1.0)

    def test_pose_decoding_of_known_transforms(self):
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        c, s_ = math.cos(math.radians(30)), math.sin(math.radians(30))
        moved = [2 * c, -s_, 0, 5, 2 * s_, c, 0, 6, 0, 0, 3, 7, 0, 0, 0, 1]      # scale (2, 1, 3), turn 30 deg, translate (5, 6, 7)
        pose = AA.decode_pose(identity, moved)
        self.assertEqual(pose["translation"], [5, 6, 7])
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(pose["scale"], [2, 1, 3])))
        self.assertAlmostEqual(pose["turn"], 30.0, places=9)

    def test_a_turn_beyond_half_a_circle_reads_back_as_authored(self):
        s = self.spec()
        AA.set_key(s, OID, "turn", 4.0, AA.spec_value("turn", 270), fps=24)
        self.assertAlmostEqual(AA.unwrap_turn(-90.0, s, OID, 4.0), 270.0)


@unittest.skipUnless(AVAILABLE, SKIP)
class Committed(unittest.TestCase):
    """The commands against the real agent: what is stored, what is evaluated, undo and persistence."""

    def setUp(self) -> None:
        self.p = Project.create("Author")
        level = self.p.manifest["levels"][0]["id"]
        self.a = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        self.b = self.p.execute("model.add_wall", {"start": [0, 3000], "end": [4000, 3000], "level": level, "thickness": 200, "height": 3})["result"]["id"]

    def key(self, prop, t, value, oid=None, **extra):
        return self.p.execute("anim.key", {"id": oid or self.a, "property": prop, "time": t, "value": value, **extra})

    def z0(self, t, oid=None):
        verts = next(o for o in self.p.scene_data(time=t)["objects"] if o["id"] == (oid or self.a))["vertices"]
        return min(v[2] for v in verts)

    def test_the_first_key_creates_the_animation_with_the_settings_length(self):
        self.assertIsNone(self.p.animation_data()["animation"])
        self.p.execute("anim.settings", {"duration": 6})
        self.assertIsNone(self.p.animation_data()["animation"])                     # settings alone do not create one
        self.key("translation", 3.0, [0, 0, 2])
        data = self.p.animation_data()
        self.assertEqual(data["animation"]["duration"], 6.0)
        self.assertEqual([k["time"] for k in data["lanes"][0]["keys"]], [0.0, 3.0])

    def test_two_keys_move_the_wall_between_them_and_hold_after(self):
        """The case from the bug report: the wall in a different state at the start and at the end."""
        base = self.z0(0)
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 0.0, [0, 0, 0], interpolation="linear")
        self.key("translation", 4.0, [0, 0, 2])
        for t, dz in ((0, 0), (1, 0.5), (2, 1.0), (4, 2.0), (100, 2.0)):
            self.assertAlmostEqual(self.z0(t) - base, dz, places=9)

    def test_smooth_interpolation_is_a_real_ease_in_and_out(self):
        base = self.z0(0)
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 4.0, [0, 0, 4])
        self.assertAlmostEqual(self.z0(1) - base, 4 * (3 * 0.25 ** 2 - 2 * 0.25 ** 3), places=9)       # smoothstep
        self.assertAlmostEqual(self.z0(2) - base, 2.0, places=9)
        self.assertLess(self.z0(0.5) - base, 4 * 0.5 / 4)                                              # slower than linear at the start

    def test_a_turn_rotates_about_the_vertical_axis_through_the_element_origin(self):
        self.p.execute("anim.settings", {"duration": 2})
        self.key("turn", 2.0, 90, interpolation="linear")
        pose = self.p.animation_pose(self.a, 1.0)
        self.assertAlmostEqual(pose["pose"]["turn"], 45.0, places=6)
        self.assertEqual(pose["animated"], ["turn"])
        self.assertAlmostEqual(self.p.animation_pose(self.a, 2.0)["pose"]["turn"], 90.0, places=6)

    def test_a_door_or_window_swings_about_its_hinge_edge(self):
        """The object origin of a door or window is its hinge, so a Turn swings it as the 2D drawing does, not about the middle of the leaf."""
        for kind in ("door", "window"):
            for hinge in ("start", "end"):
                self.setUp()
                oid = self.p.execute(f"model.add_{kind}", {"wall": self.a, "offset": 2.0, "width": 900})["result"]["id"]
                self.p.execute("model.set_property", {"id": oid, "key": "hinge", "value": hinge})
                self.p.execute("anim.settings", {"duration": 2})
                self.key("turn", 2.0, 90, oid=oid, interpolation="linear")
                edge = 1.55 if hinge == "start" else 2.45
                before = next(o for o in self.p.scene_data(time=0)["objects"] if o["id"] == oid)["vertices"]
                after = next(o for o in self.p.scene_data(time=2)["objects"] if o["id"] == oid)["vertices"]
                self.assertAlmostEqual(min(v[0] for v in before), 1.55, places=6)         # at rest it fills its opening
                self.assertAlmostEqual(max(v[0] for v in before), 2.45, places=6)
                # a quarter turn about the hinge edge lays the leaf along y: its x extent is only its own thickness, centred on the hinge
                xs = [v[0] for v in after]
                self.assertLess(max(xs) - min(xs), 0.1, (kind, hinge))
                # a window pivots on the wall's centre line; a door on the wall face (half the 200 mm wall away), where the 2D swing arc is drawn
                self.assertAlmostEqual(abs((max(xs) + min(xs)) / 2 - edge), 0.1 if kind == "door" else 0.0, places=6, msg=(kind, hinge))
                ys = [v[1] for v in after]
                self.assertAlmostEqual(max(ys) - min(ys), 0.9, places=6)

    def test_scale_and_visibility_are_evaluated(self):
        self.p.execute("anim.settings", {"duration": 4})
        self.key("scale", 4.0, [2, 1, 1])
        self.key("visibility", 3.0, False)
        end = self.p.animation_pose(self.a, 4.0)
        self.assertAlmostEqual(end["pose"]["scale"][0], 2.0, places=9)
        self.assertFalse(end["pose"]["visibility"])
        self.assertTrue(self.p.animation_pose(self.a, 2.0)["pose"]["visibility"])
        self.assertNotIn(self.a, {o["id"] for o in self.p.scene_data(time=3.5)["objects"]})
        self.assertIn(self.a, {o["id"] for o in self.p.scene_data(time=2.9)["objects"]})

    def test_pose_reports_which_properties_are_keyed_at_that_frame(self):
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 2.0, [0, 0, 1])
        self.assertEqual(self.p.animation_pose(self.a, 2.0)["keyed"], ["translation"])
        self.assertEqual(self.p.animation_pose(self.a, 1.0)["keyed"], [])
        rest = self.p.animation_pose(self.b, 1.0)
        self.assertEqual((rest["animated"], rest["pose"]["translation"], rest["pose"]["visibility"]), ([], [0.0, 0.0, 0.0], True))

    def test_pose_sets_several_properties_in_one_undo_step(self):
        self.p.execute("anim.settings", {"duration": 4})
        revision, depth = self.p.revision, len(self.p.undo_stack)
        self.p.execute("anim.pose", {"id": self.a, "time": 2.0, "values": {"translation": [1, 0, 0], "turn": 45, "scale": [1, 1, 2]}})
        self.assertEqual((self.p.revision, len(self.p.undo_stack)), (revision + 1, depth + 1))
        self.assertEqual({lane["property"] for lane in self.p.animation_data()["lanes"]}, {"translation", "turn", "scale"})
        self.p.undo()
        self.assertIsNone(self.p.animation_data()["animation"])
        self.p.redo()
        self.assertEqual(len(self.p.animation_data()["lanes"]), 3)

    def test_move_delete_and_track_removal(self):
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 1.0, [0, 0, 1])
        self.p.execute("anim.move_key", {"id": self.a, "property": "translation", "time": 1.0, "to": 3.0})
        self.assertEqual([k["time"] for k in self.p.animation_data()["lanes"][0]["keys"]], [0.0, 3.0])
        self.p.execute("anim.delete_key", {"id": self.a, "property": "translation", "time": 3.0})
        self.assertEqual([k["time"] for k in self.p.animation_data()["lanes"][0]["keys"]], [0.0])
        self.p.execute("anim.delete_track", {"id": self.a, "property": "translation"})
        self.assertEqual(self.p.animation_data()["lanes"], [])

    def test_refused_authoring_changes_nothing(self):
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 2.0, [0, 0, 1])
        revision, depth, anim = self.p.revision, len(self.p.undo_stack), self.p.animation_data()["animation"]
        bad = [("anim.key", {"id": self.a, "property": "translation", "time": 1, "value": [1, 2]}),
               ("anim.key", {"id": "nope", "property": "translation", "time": 1, "value": [1, 2, 3]}),
               ("anim.key", {"id": self.a, "property": "colour", "time": 1, "value": 1}),
               ("anim.move_key", {"id": self.a, "property": "translation", "time": 0.0, "to": 2.0}),
               ("anim.delete_key", {"id": self.a, "property": "translation", "time": 3.0}),
               ("anim.delete_track", {"id": self.b, "property": "translation"}),
               ("anim.settings", {"fps": 0}), ("anim.settings", {"fps": 24.5}), ("anim.settings", {"duration": 1}),
               ("anim.preset", {"id": self.a, "kind": "slide", "params": {}})]
        for name, payload in bad:
            with self.assertRaises((BadRequest, NotFound), msg=name + str(payload)):
                self.p.execute(name, payload)
        self.assertEqual((self.p.revision, len(self.p.undo_stack), self.p.animation_data()["animation"]), (revision, depth, anim))

    def test_shortening_below_the_last_key_is_refused_with_a_reason(self):
        self.p.execute("anim.settings", {"duration": 4})
        self.key("translation", 3.0, [0, 0, 1])
        with self.assertRaisesRegex(BadRequest, "last key"):
            self.p.execute("anim.settings", {"duration": 2})
        self.p.execute("anim.settings", {"duration": 8})
        self.assertEqual(self.p.animation_data()["animation"]["duration"], 8.0)

    def test_markers_are_stored_in_the_scene_and_listed(self):
        self.p.execute("anim.settings", {"duration": 4})
        mid = self.p.execute("anim.marker", {"time": 1.0, "name": "Wall rises"})["result"]["id"]
        self.assertEqual([(m["name"], m["time"]) for m in self.p.animation_data()["markers"]], [("Wall rises", 1.0)])
        self.p.execute("anim.delete_marker", {"marker": mid})
        self.assertEqual(self.p.animation_data()["markers"], [])

    def test_presets_animate_without_any_hand_typed_keys(self):
        base = self.z0(0)
        self.p.execute("anim.settings", {"duration": 4})
        self.p.execute("anim.preset", {"id": self.a, "kind": "slide", "start": 0, "end": 4, "params": {"by": [0, 0, 3]}})
        self.assertAlmostEqual(self.z0(4) - base, 3.0, places=9)
        self.p.execute("anim.preset", {"id": self.b, "kind": "disappear", "start": 1, "end": 3})
        self.assertIn(self.b, {o["id"] for o in self.p.scene_data(time=0.5)["objects"]})
        self.assertNotIn(self.b, {o["id"] for o in self.p.scene_data(time=3.0)["objects"]})

    def test_frame_rate_snaps_key_times(self):
        self.p.execute("anim.settings", {"fps": 10, "duration": 4})
        self.key("translation", 1.04, [0, 0, 1])
        self.assertEqual([k["time"] for k in self.p.animation_data()["lanes"][0]["keys"]], [0.0, 1.0])

    def test_settings_survive_save_and_reopen_with_the_animation(self):
        self.p.execute("anim.settings", {"fps": 30, "loop": True, "duration": 5})
        self.key("translation", 5.0, [0, 0, 2])
        self.key("turn", 5.0, 180)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.spadra"
            self.p.save(path)
            q = Project.open(path)
        self.assertEqual(q.animation_settings(), {"fps": 30, "loop": True, "duration": 5.0})
        self.assertEqual(q.animation_data()["lanes"], self.p.animation_data()["lanes"])
        self.assertAlmostEqual(q.animation_pose(self.a, 5.0)["pose"]["turn"], 180.0, places=6)
        self.assertTrue(q.check()["ok"])

    def test_undoing_settings_restores_them(self):
        self.p.execute("anim.settings", {"fps": 12})
        self.p.undo()
        self.assertEqual(self.p.animation_settings()["fps"], AA.DEFAULT_FPS)

    def test_keys_made_by_hand_through_the_agent_still_show_as_lanes(self):
        spec = {"duration": 2.0, "tracks": [{"object_id": self.a, "property": "translation", "interpolation": "linear",
                                              "keyframes": [{"time": 0, "value": [0, 0, 0]}, {"time": 2, "value": [0, 0, 1]}]}]}
        self.p.execute("anim.edit", {"operation": "set_animation", "payload": {"animation": spec}})
        lane = self.p.animation_data()["lanes"][0]
        self.assertEqual((lane["property"], lane["interpolation"], len(lane["keys"])), ("translation", "linear", 2))
        self.key("translation", 1.0, [0, 0, 5])                                    # authoring continues from an agent-made animation
        self.assertEqual(len(self.p.animation_data()["lanes"][0]["keys"]), 3)

    def test_only_elements_with_geometry_can_be_animated(self):
        with self.assertRaises(BadRequest):
            self.key("translation", 1.0, [0, 0, 1], oid="00000000-0000-4000-8000-000000000000")


class WithoutAgent(unittest.TestCase):
    def test_authoring_is_refused_honestly(self):
        saved = AB.BRIDGE
        bridge = AB.AnimationBridge()
        bridge._tried, bridge._reason = True, "ModuleNotFoundError: No module named 'src.agents'"
        AB.BRIDGE = bridge
        try:
            p = Project.create("X")
            level = p.manifest["levels"][0]["id"]
            wall = p.execute("model.add_wall", {"start": [0, 0], "end": [1000, 0], "level": level, "thickness": 100, "height": 3})["result"]["id"]
            with self.assertRaisesRegex(BadRequest, "not available"):
                p.execute("anim.key", {"id": wall, "property": "translation", "time": 1.0, "value": [0, 0, 1]})
            self.assertEqual(p.animation_data()["lanes"], [])
        finally:
            AB.BRIDGE = saved


if __name__ == "__main__":
    unittest.main()
