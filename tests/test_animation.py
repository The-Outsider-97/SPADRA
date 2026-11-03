"""Animation through the real SLAI Animation Agent. Skipped (not faked) where the SLAI checkout has no ``src/agents``: the agent is the
subject of these tests, so a stand-in would prove nothing. Run from a SLAI root that contains the agent, e.g.
``python -m unittest discover -s applications/SPADRA/tests -t .``"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import animation_bridge as AB
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, DependencyMissing

AVAILABLE = AB.BRIDGE.available
SKIP = f"the SLAI Animation Agent is not importable here ({AB.BRIDGE.reason})"


def z_range(scene, oid):
    verts = next(o for o in scene["objects"] if o["id"] == oid)["vertices"]
    return min(v[2] for v in verts), max(v[2] for v in verts)


@unittest.skipUnless(AVAILABLE, SKIP)
class Animated(unittest.TestCase):
    """Two 4 m walls 2 m apart, 3 m high. The first slides up 2 m over 4 s (linear); the second is visible until t = 2 s."""

    def setUp(self) -> None:
        self.p = Project.create("Anim")
        level = self.p.manifest["levels"][0]["id"]
        self.a = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        self.b = self.p.execute("model.add_wall", {"start": [0, 2000], "end": [4000, 2000], "level": level, "thickness": 200, "height": 3})["result"]["id"]

    def spec(self):
        return {"duration": 4.0, "tracks": [
            {"object_id": self.a, "property": "translation", "interpolation": "linear", "keyframes": [{"time": 0, "value": [0, 0, 0]}, {"time": 4, "value": [0, 0, 2]}]},
            {"object_id": self.b, "property": "visibility", "interpolation": "step", "keyframes": [{"time": 0, "value": True}, {"time": 2, "value": False}]}]}

    def edit(self, operation, **payload):
        return self.p.execute("anim.edit", {"operation": operation, "payload": payload})

    def animate(self):
        self.edit("set_animation", animation=self.spec())

    # ------------------------------------------------------------------------------------------------------------------ the agent
    def test_the_agent_is_the_real_one_and_reports_its_operations(self):
        status = AB.BRIDGE.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["agent"], "SLAI Animation Agent")
        for op in ("set_animation", "add_keyframe", "update_keyframe", "delete_keyframe", "add_track", "delete_track", "set_duration"):
            self.assertIn(op, status["operations"])
        self.assertIn("physics_simulation", status["unsupported"])                   # what the agent says it cannot do, it says
        self.assertEqual(AB.BRIDGE._agent.__class__.__module__, "src.agents.animation_agent")

    def test_without_an_animation_the_scene_is_the_model_and_time_changes_nothing(self):
        static = self.p.scene_data()
        self.assertEqual(self.p.scene_data(time=3.0), static)
        self.assertIsNone(self.p.animation_data()["animation"])

    # ------------------------------------------------------------------------------------------------------------------ evaluation
    def test_a_linear_translation_moves_the_wall_by_exactly_the_interpolated_amount(self):
        base = z_range(self.p.scene_data(), self.a)
        self.animate()
        for t, dz in ((0, 0.0), (1, 0.5), (2, 1.0), (3, 1.5), (4, 2.0)):
            lo, hi = z_range(self.p.scene_data(time=t), self.a)
            self.assertAlmostEqual(lo - base[0], dz, places=9, msg=f"t={t}")
            self.assertAlmostEqual(hi - base[1], dz, places=9, msg=f"t={t}")

    def test_time_beyond_the_last_keyframe_holds_the_last_pose(self):
        base = z_range(self.p.scene_data(), self.a)
        self.animate()
        self.assertAlmostEqual(z_range(self.p.scene_data(time=100), self.a)[0] - base[0], 2.0, places=9)

    def test_a_step_visibility_track_hides_the_wall_from_its_keyframe(self):
        self.animate()
        before = {o["id"] for o in self.p.scene_data(time=1.9)["objects"]}
        after = {o["id"] for o in self.p.scene_data(time=2.0)["objects"]}
        self.assertEqual(before, {self.a, self.b})
        self.assertEqual(after, {self.a})
        self.assertEqual(self.p.scene_data(time=2.0)["time"], 2.0)

    def test_bounds_follow_the_animated_pose(self):
        self.animate()
        self.assertAlmostEqual(self.p.scene_data(time=4)["bounds"][1][2] - self.p.scene_data(time=0)["bounds"][1][2], 2.0, places=9)

    def test_a_negative_time_is_refused(self):
        self.animate()
        with self.assertRaises(BadRequest):
            self.p.scene_data(time=-1)

    # ------------------------------------------------------------------------------------------------------------------ editing
    def test_an_edit_is_one_revision_and_undo_restores_the_exact_previous_animation(self):
        self.animate()
        before, revision = self.p.animation_data()["animation"], self.p.revision
        out = self.edit("add_keyframe", track_index=0, keyframe={"time": 2, "value": [0, 0, 0]})
        self.assertEqual(out["revision"], revision + 1)
        frames = self.p.animation_data()["animation"]["tracks"][0]["keyframes"]
        self.assertEqual([f["time"] for f in frames], [0, 2, 4])                    # kept in time order by the agent
        self.p.undo()
        self.assertEqual(self.p.animation_data()["animation"], before)
        self.p.redo()
        self.assertEqual(len(self.p.animation_data()["animation"]["tracks"][0]["keyframes"]), 3)

    def test_updating_and_deleting_keyframes_changes_what_is_evaluated(self):
        self.animate()
        base = z_range(self.p.scene_data(), self.a)
        self.edit("update_keyframe", track_index=0, keyframe_index=1, keyframe={"time": 4, "value": [0, 0, 6]})
        self.assertAlmostEqual(z_range(self.p.scene_data(time=2), self.a)[0] - base[0], 3.0, places=9)
        self.edit("delete_keyframe", track_index=0, keyframe_index=1)
        self.assertAlmostEqual(z_range(self.p.scene_data(time=2), self.a)[0] - base[0], 0.0, places=9)     # one keyframe at 0: it holds

    def test_tracks_can_be_added_and_deleted_and_the_duration_changed(self):
        self.animate()
        self.edit("add_track", track={"object_id": self.b, "property": "translation", "interpolation": "linear",
                                       "keyframes": [{"time": 0, "value": [0, 0, 0]}, {"time": 1, "value": [1, 0, 0]}]})
        self.assertEqual(len(self.p.animation_data()["animation"]["tracks"]), 3)
        self.edit("delete_track", track_index=2)
        self.assertEqual(len(self.p.animation_data()["animation"]["tracks"]), 2)
        self.edit("set_duration", duration=10.0)
        self.assertEqual(self.p.animation_data()["animation"]["duration"], 10.0)
        self.edit("clear_animation")
        self.assertIsNone(self.p.animation_data()["animation"])

    def test_a_refused_edit_leaves_the_project_exactly_as_it_was(self):
        self.animate()
        revision, undo_depth, animation = self.p.revision, len(self.p.undo_stack), self.p.animation_data()["animation"]
        for operation, payload in (("add_keyframe", {"track_index": 9, "keyframe": {"time": 1, "value": [0, 0, 1]}}),                 # no such track
                                   ("add_keyframe", {"track_index": 0, "keyframe": {"time": 4, "value": [0, 0, 1]}}),                 # duplicate time
                                   ("add_track", {"track": {"object_id": "00000000-0000-4000-8000-000000000000", "property": "translation",
                                                            "keyframes": [{"time": 0, "value": [0, 0, 0]}]}}),                       # no such object
                                   ("set_duration", {"duration": -1}), ("fly", {}), ("evaluate", {"time": 1})):                       # not an edit
            with self.assertRaises(BadRequest, msg=operation):
                self.edit(operation, **payload)
        self.assertEqual((self.p.revision, len(self.p.undo_stack), self.p.animation_data()["animation"]), (revision, undo_depth, animation))

    def test_deleting_an_animated_element_takes_its_animation_with_it_and_undo_brings_both_back(self):
        self.animate()
        tracks = len(self.p.animation_data()["animation"]["tracks"])
        self.assertEqual(tracks, 2)
        self.p.execute("model.delete", {"id": self.b})                              # earlier versions refused this with "missing scene object"
        self.assertNotIn(self.b, self.p.model.query_ids())
        left = (self.p.animation_data().get("animation") or {}).get("tracks", [])
        self.assertTrue(all(t["object_id"] != self.b for t in left))
        self.p.undo()
        self.assertIn(self.b, self.p.model.query_ids())
        self.assertEqual(len(self.p.animation_data()["animation"]["tracks"]), tracks)

    def test_a_stale_project_revision_is_refused_before_the_agent_is_asked(self):
        self.animate()
        with self.assertRaises(Exception) as caught:
            self.p.execute("anim.edit", {"operation": "set_duration", "payload": {"duration": 9.0}}, expected_revision=self.p.revision - 1)
        self.assertEqual(type(caught.exception).__name__, "Conflict")
        self.assertEqual(self.p.animation_data()["animation"]["duration"], 4.0)

    # ------------------------------------------------------------------------------------------------------------------ persistence
    def test_the_animation_survives_save_and_reopen(self):
        self.animate()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.spadra"
            self.p.save(path)
            q = Project.open(path)
        self.assertEqual(q.animation_data()["animation"], self.p.animation_data()["animation"])
        self.assertEqual(q.scene_data(time=3)["objects"][0]["vertices"], self.p.scene_data(time=3)["objects"][0]["vertices"])
        self.assertTrue(q.check()["ok"])

    def test_the_project_check_still_passes_with_an_animation(self):
        self.animate()
        self.assertTrue(self.p.check()["ok"])


class Unavailable(unittest.TestCase):
    """What happens where the agent cannot be loaded: a plain, honest refusal."""

    def setUp(self) -> None:
        self.bridge = AB.AnimationBridge()
        self.bridge._tried, self.bridge._reason = True, "ModuleNotFoundError: No module named 'src.agents'"
        self.saved, AB.BRIDGE = AB.BRIDGE, self.bridge

    def tearDown(self) -> None:
        AB.BRIDGE = self.saved

    def test_status_says_why(self):
        status = self.bridge.status()
        self.assertFalse(status["available"])
        self.assertIn("src.agents", status["reason"])

    def test_requests_raise_a_dependency_error_not_a_fake_result(self):
        with self.assertRaises(DependencyMissing):
            self.bridge.request(Project.create("X").model, "get_animation")
        with self.assertRaises(DependencyMissing):
            self.bridge.evaluate(Project.create("X").model, 1.0)

    def test_the_edit_command_is_refused(self):
        p = Project.create("X")
        with self.assertRaisesRegex(BadRequest, "not available"):
            p.execute("anim.edit", {"operation": "set_animation", "payload": {"animation": {"duration": 1, "tracks": []}}})

    def test_a_file_that_already_holds_an_animation_still_opens_and_shows_the_static_model(self):
        p = Project.create("X")
        self.assertEqual(p.scene_data(), p.scene_data(time=None))
        self.assertFalse(p.animation_data()["agent"]["available"])


if __name__ == "__main__":
    unittest.main()
