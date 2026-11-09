"""Door kinematics and time-dependent parameters (report 8.4 and 8.5): the explicit smoothstep law, the motion verifier, hinge-anchored resizing and the
sinusoidal / linear motion laws, each checked against the exact formula. Evaluated animation needs the SLAI Animation Agent; those tests skip where it is absent."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import animation_bridge as AB
from applications.SPADRA import door_swing as D
from applications.SPADRA import motion_laws as ML
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

from .harness import ServerCase

AVAILABLE = AB.BRIDGE.available
SKIP = f"the SLAI Animation Agent is not importable here ({AB.BRIDGE.reason})"


def door_project(*, angle: float = 0.0, hinge: str = "start", swing: str = "left", width: int = 900, duration: float = 2.0):
    p = Project.create("Kinematics")
    level = p.manifest["levels"][0]["id"]
    x1, y1 = 6.0 * math.cos(math.radians(angle)), 6.0 * math.sin(math.radians(angle))
    wall = p.execute("model.add_wall", {"start": [0, 0], "end": [x1 * 1000, y1 * 1000], "level": level, "thickness": 200, "height": 3})["result"]["id"]
    door = p.execute("model.add_door", {"wall": wall, "offset": 3.0, "width": width, "hinge": hinge, "swing": swing})["result"]["id"]
    return p, wall, door


class Law(unittest.TestCase):
    def test_smoothstep_values_for_a_90_degree_swing_over_two_seconds(self):
        got = [D.eased_angle(t, 0.0, 2.0, 90.0) for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
        for g, want in zip(got, (0.0, 14.0625, 45.0, 75.9375, 90.0)):
            self.assertAlmostEqual(g, want, places=9)

    def test_smoothstep_is_clamped_and_has_zero_end_slopes(self):
        self.assertEqual((D.smoothstep(-1), D.smoothstep(2)), (0.0, 1.0))
        h = 1e-6
        self.assertAlmostEqual((D.smoothstep(h) - D.smoothstep(0)) / h, 0.0, places=4)
        self.assertAlmostEqual((D.smoothstep(1) - D.smoothstep(1 - h)) / h, 0.0, places=4)

    def test_an_empty_time_span_is_refused(self):
        with self.assertRaises(BadRequest):
            D.eased_angle(1.0, 1.0, 1.0, 90.0)


@unittest.skipUnless(AVAILABLE, SKIP)
class Verified(unittest.TestCase):
    def test_a_swung_door_passes_every_check(self):
        for hinge, swing in (("start", "left"), ("start", "right"), ("end", "left"), ("end", "right")):
            with self.subTest(hinge=hinge, swing=swing):
                p, _, door = door_project(hinge=hinge, swing=swing)
                p.execute("anim.settings", {"duration": 2.0, "fps": 24})
                p.execute("anim.swing_door", {"id": door, "degrees": 90, "start": 0, "end": 2.0})
                v = D.verify_motion(p, door)
                self.assertTrue(v["ok"], v["checks"])
                self.assertLess(v["max_angle_error_deg"], 1e-6)
                self.assertLess(v["max_hinge_drift_m"], 1e-9)
                self.assertGreaterEqual(v["samples"], 49)

    def test_the_motion_on_a_rotated_wall_is_checked_in_the_walls_frame(self):
        p, _, door = door_project(angle=37.0)
        p.execute("anim.settings", {"duration": 2.0, "fps": 24})
        p.execute("anim.swing_door", {"id": door, "degrees": 75, "start": 0.5, "end": 1.5})
        self.assertTrue(D.verify_motion(p, door)["ok"])

    def test_a_door_with_no_swing_is_refused_not_passed(self):
        p, _, door = door_project()
        with self.assertRaises(BadRequest):
            D.verify_motion(p, door)

    def test_a_motion_that_is_not_the_smoothstep_law_fails_the_law_check(self):
        p, _, door = door_project()
        p.execute("anim.settings", {"duration": 2.0, "fps": 24})
        p.execute("anim.swing_door", {"id": door, "degrees": 90, "start": 0, "end": 2.0})
        p.execute("anim.interpolation", {"id": door, "property": "turn", "interpolation": "linear"})
        v = D.verify_motion(p, door)
        self.assertFalse(v["ok"])
        self.assertFalse(v["checks"]["follows_smoothstep_law"])
        self.assertTrue(v["checks"]["hinge_axis_fixed_in_world"])             # still the same pivot: only the timing differs
        self.assertGreater(v["max_angle_error_deg"], 1.0)

    def test_the_sample_count_is_bounded(self):
        p, _, door = door_project()
        p.execute("anim.swing_door", {"id": door, "degrees": 90, "start": 0, "end": 1.0})
        for bad in (2, 1001, True, "9"):
            with self.assertRaises(BadRequest):
                D.verify_motion(p, door, samples=bad)


class HingedResize(unittest.TestCase):
    def test_hinge_anchor_keeps_the_hinge_and_moves_the_free_edge(self):
        for hinge in ("start", "end"):
            for angle in (0.0, 37.0):
                with self.subTest(hinge=hinge, angle=angle):
                    p, _, door = door_project(hinge=hinge, angle=angle)
                    before = D.hinge_position(p, door)
                    r = p.execute("model.resize_door", {"id": door, "width": 1200})["result"]
                    after = D.hinge_position(p, door)
                    self.assertAlmostEqual(r["hinge_moved_m"], 0.0, places=9)
                    self.assertAlmostEqual(math.dist(before, after), 0.0, places=9)
                    self.assertAlmostEqual(r["width"], 1.2, places=9)
                    expected = 3.0 - 0.45 + 0.6 if hinge == "start" else 3.0 + 0.45 - 0.6
                    self.assertAlmostEqual(r["offset"], expected, places=9)

    def test_shrinking_also_keeps_the_hinge(self):
        p, _, door = door_project(hinge="end")
        before = D.hinge_position(p, door)
        p.execute("model.resize_door", {"id": door, "width": 700})
        self.assertAlmostEqual(math.dist(before, D.hinge_position(p, door)), 0.0, places=9)

    def test_centre_anchor_is_what_a_plain_width_edit_does(self):
        p, _, door = door_project()
        r = p.execute("model.resize_door", {"id": door, "width": 800, "anchor": "centre"})["result"]
        self.assertAlmostEqual(r["offset"], 3.0, places=9)
        self.assertAlmostEqual(r["hinge_moved_m"], 0.05, places=9)             # hinge at 2.55 -> 2.60: the centre stays, so the hinge edge moves by half the change

    def test_a_door_too_wide_for_the_wall_is_refused_and_left_alone(self):
        p, wall, door = door_project()
        before_hinge, before_rev = D.hinge_position(p, door), p.manifest.get("revision")
        with self.assertRaises(BadRequest):
            p.execute("model.resize_door", {"id": door, "width": 20000})
        self.assertEqual(D.hinge_position(p, door), before_hinge)
        self.assertEqual(p.manifest.get("revision"), before_rev)

    def test_only_a_door_and_known_anchors_are_accepted(self):
        p, wall, door = door_project()
        with self.assertRaises(BadRequest):
            p.execute("model.resize_door", {"id": door, "width": 900, "anchor": "left"})
        with self.assertRaises(BadRequest):
            p.execute("model.resize_door", {"id": wall, "width": 900})

    def test_undo_restores_the_door(self):
        p, _, door = door_project()
        before = D.hinge_position(p, door)
        p.execute("model.resize_door", {"id": door, "width": 1100})
        p.undo()
        self.assertEqual(D.hinge_position(p, door), before)

    @unittest.skipUnless(AVAILABLE, SKIP)
    def test_the_swing_still_verifies_and_stays_on_its_pivot_after_a_hinged_resize(self):
        p, _, door = door_project()
        p.execute("anim.settings", {"duration": 2.0, "fps": 24})
        p.execute("anim.swing_door", {"id": door, "degrees": 90, "start": 0, "end": 2.0})
        before = D.hinge_position(p, door)
        p.execute("model.resize_door", {"id": door, "width": 1000})
        v = D.verify_motion(p, door)
        self.assertTrue(v["ok"], v["checks"])
        self.assertAlmostEqual(math.dist(before, D.hinge_position(p, door)), 0.0, places=9)


@unittest.skipUnless(AVAILABLE, SKIP)
class TimeLaws(unittest.TestCase):
    def setUp(self):
        self.p, self.wall, self.door = door_project()
        self.p.execute("anim.settings", {"duration": 4.0, "fps": 24})

    def evaluated_turn(self, t):
        base = self.p.model.world_transform(self.door)
        wt = AB.BRIDGE.evaluate(self.p.model, t).get_object_state(self.door)["world_transform"]
        return math.degrees(math.atan2(wt[4], wt[0]) - math.atan2(base[4], base[0]))

    def test_sinusoid_matches_theta0_plus_a_sin_wt_between_the_keys(self):
        r = self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 30, "period": 2.0, "offset": 5, "start": 0, "end": 4.0})["result"]
        self.assertLess(r["max_error"], 0.05)
        for t in (0.1, 0.37, 0.5, 1.0, 1.23, 1.5, 2.9, 3.333):                  # off-key times, so this is interpolation and not the keys themselves
            want = 5 + 30 * math.sin(2 * math.pi * t / 2.0)
            self.assertAlmostEqual(self.evaluated_turn(t), want, delta=0.05, msg=f"t={t}")

    def test_the_error_falls_with_more_keys_and_is_what_the_cubic_error_term_predicts(self):
        errors = []
        for k in (6, 12, 24):
            r = self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 30, "period": 2.0, "keys_per_period": k, "tolerance": 5.0})["result"]
            errors.append(r["max_error"])
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])
        self.assertLess(errors[2], errors[1] / 8)                                  # Hermite with exact tangents: error ~ h^4, so doubling the keys cuts it by about 16
        self.assertLess(errors[1], 1.96e-4 * 30 * 1.5)                              # (2 pi / k)^4 / 384 * A, with slack for frame-rounded key times

    def test_too_coarse_a_sampling_is_refused_and_leaves_the_animation_alone(self):
        self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 30, "period": 2.0, "tolerance": 0.05})
        keys_before = [len(t["keyframes"]) for t in self.p.model.animation()["tracks"]]
        with self.assertRaises(Conflict) as ctx:
            self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 30, "period": 2.0, "keys_per_period": 4, "tolerance": 0.001})
        self.assertIn("more keys", str(getattr(ctx.exception, "hint", "") or ctx.exception).lower())
        self.assertEqual([len(t["keyframes"]) for t in self.p.model.animation()["tracks"]], keys_before)

    def test_linear_law_is_exact(self):
        r = self.p.execute("anim.law", {"id": self.door, "kind": "linear", "velocity": [0.1, 0.0, 0.02], "offset": [0.0, 0.0, 0.0], "start": 0, "end": 4.0})["result"]
        self.assertEqual(r["keys"], 2)
        self.assertLess(r["max_error"], 1e-9)
        base = self.p.model.world_transform(self.door)
        for t in (0.0, 1.0, 2.5, 4.0):
            wt = AB.BRIDGE.evaluate(self.p.model, t).get_object_state(self.door)["world_transform"]
            self.assertAlmostEqual(wt[3] - base[3], 0.1 * t, places=9)
            self.assertAlmostEqual(wt[11] - base[11], 0.02 * t, places=9)

    def test_the_value_is_held_outside_the_window(self):
        self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 30, "period": 1.0, "start": 1.0, "end": 2.0})
        self.assertAlmostEqual(self.evaluated_turn(0.0), 0.0, places=6)
        self.assertAlmostEqual(self.evaluated_turn(3.9), 30 * math.sin(2 * math.pi * 1.0), places=6)       # sin(2 pi) = 0

    def test_a_law_replaces_the_earlier_track_rather_than_adding_to_it(self):
        self.p.execute("anim.swing_door", {"id": self.door, "degrees": 90, "start": 0, "end": 2.0})
        self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 10, "period": 2.0})
        turns = [t for t in self.p.model.animation()["tracks"] if t["property"] == "rotation_angle"]
        self.assertEqual(len(turns), 1)
        self.assertAlmostEqual(self.evaluated_turn(0.5), 10.0, delta=0.05)

    def test_undo_restores_the_earlier_animation(self):
        self.p.execute("anim.swing_door", {"id": self.door, "degrees": 90, "start": 0, "end": 2.0})
        before = self.evaluated_turn(1.0)
        self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 10, "period": 2.0})
        self.p.undo()
        self.assertAlmostEqual(self.evaluated_turn(1.0), before, places=9)

    def test_bad_input_is_refused_with_a_reason(self):
        cases = [{"kind": "spiral"}, {"kind": "linear"}, {"kind": "linear", "velocity": [0, 0, 0]}, {"kind": "linear", "velocity": [1, 0]},
                 {"kind": "sinusoid", "amplitude": 0, "period": 1}, {"kind": "sinusoid", "amplitude": 30}, {"kind": "sinusoid", "amplitude": 30, "period": 0},
                 {"kind": "sinusoid", "amplitude": 170, "period": 1, "offset": 20}, {"kind": "sinusoid", "amplitude": 30, "period": 1, "start": 2, "end": 1},
                 {"kind": "sinusoid", "amplitude": 30, "period": 1, "end": 99}, {"kind": "sinusoid", "amplitude": 30, "period": 1, "keys_per_period": 2},
                 {"kind": "sinusoid", "amplitude": float("nan"), "period": 1}]
        for c in cases:
            with self.subTest(c=c):
                with self.assertRaises(BadRequest):
                    self.p.execute("anim.law", {"id": self.door, **c})

    def test_a_period_shorter_than_the_frame_resolution_is_refused(self):
        with self.assertRaises(BadRequest):
            self.p.execute("anim.law", {"id": self.door, "kind": "sinusoid", "amplitude": 10, "period": 0.05, "keys_per_period": 12})


@unittest.skipUnless(AVAILABLE, SKIP)
class PresetEasing(unittest.TestCase):
    """A preset that starts after 0 s has a hold key at 0 s; its start key must still ease (zero slope), or the motion overshoots before it begins."""
    def test_a_late_slide_stays_between_its_end_points(self):
        p, _, door = door_project()
        p.execute("anim.settings", {"duration": 4.0, "fps": 24})
        p.execute("anim.preset", {"id": door, "kind": "slide", "params": {"by": [2.0, 0, 0]}, "start": 1.0, "end": 3.0})
        base = p.model.world_transform(door)
        xs = [AB.BRIDGE.evaluate(p.model, t / 24).get_object_state(door)["world_transform"][3] - base[3] for t in range(0, 97)]
        self.assertGreaterEqual(min(xs), -1e-9)
        self.assertLessEqual(max(xs), 2.0 + 1e-9)
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(xs, xs[1:])))
        self.assertAlmostEqual(xs[48], 1.0, places=9)                             # halfway through the window the smoothstep is at 1/2



@unittest.skipUnless(AVAILABLE, SKIP)
class Route(ServerCase):
    def test_verify_route_and_commands_over_http(self):
        a = self.api
        a.post("/api/project/new", {"name": "Kinematics", "unit": "mm"})
        level = a.get("/api/project/tree")["levels"][0]["id"]
        wall = a.cmd("model.add_wall", {"start": [0, 0], "end": [6000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
        door = a.cmd("model.add_door", {"wall": wall, "offset": 3.0, "width": 900})["result"]["id"]
        self.assertEqual(a.call("GET", f"/api/doors/{door}/swing/verify")[0], 400)               # no swing animation yet: refused, not passed
        a.cmd("anim.settings", {"duration": 2.0, "fps": 24})
        a.cmd("anim.swing_door", {"id": door, "degrees": 90, "start": 0.25, "end": 1.75})
        status, out = a.call("GET", f"/api/doors/{door}/swing/verify?samples=25")
        self.assertEqual(status, 200)
        self.assertTrue(out["data"]["ok"], out["data"]["checks"])
        self.assertEqual(a.call("GET", f"/api/doors/{door}/swing/verify?samples=abc")[0], 400)
        resized = a.cmd("model.resize_door", {"id": door, "width": 1100})["result"]
        self.assertAlmostEqual(resized["hinge_moved_m"], 0.0, places=9)
        law = a.cmd("anim.law", {"id": door, "kind": "sinusoid", "amplitude": 20, "period": 1.0})["result"]
        self.assertLess(law["max_error"], 0.05)


if __name__ == "__main__":
    unittest.main()
