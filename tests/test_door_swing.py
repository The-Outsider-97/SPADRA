"""Hinged-door swing: the direction from hinge and swing side, the swept-leaf clearance check, and the command that writes the motion.
The pure geometry and the plan run everywhere; keys evaluated by SGEO need the SLAI Animation Agent and are skipped where it is absent."""
from __future__ import annotations

import unittest

from applications.SPADRA import animation_bridge as AB
from applications.SPADRA import door_swing as D
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest, Conflict

AVAILABLE = AB.BRIDGE.available
SKIP = f"the SLAI Animation Agent is not importable here ({AB.BRIDGE.reason})"
LEAF = [(0, -0.025), (0.9, -0.025), (0.9, 0.025), (0, 0.025)]


class Geometry(unittest.TestCase):
    def test_turn_sign_follows_hinge_and_swing_side(self):
        self.assertEqual([D.turn_sign(h, s) for h in ("start", "end") for s in (1, -1)], [1, -1, -1, 1])

    def test_sweep_stops_before_a_wall_at_the_right_angle(self):
        wall = [(-1, 0.5), (2, 0.5), (2, 0.7), (-1, 0.7)]
        r = D.sweep(LEAF, (0, 0), 90, {"w": wall})
        self.assertEqual(r["obstructions"], ["w"])
        self.assertEqual((r["clear_to"], r["obstructed_at"]), (32.0, 33.0))          # tip height 0.498 m at 32 degrees, 0.512 m at 33 (wall face at 0.5)

    def test_sweeping_the_other_way_is_clear(self):
        wall = [(-1, 0.5), (2, 0.5), (2, 0.7), (-1, 0.7)]
        r = D.sweep(LEAF, (0, 0), -90, {"w": wall})
        self.assertEqual((r["clear_to"], r["obstructions"]), (-90, []))

    def test_touching_is_not_hitting(self):
        wall = [(-1, -1), (2, -1), (2, -0.025), (-1, -0.025)]                       # flush against the leaf's lower face at rest; the leaf turns away from it
        self.assertEqual(D.sweep(LEAF, (0, 0), 60, {"w": wall})["obstructions"], [])
        self.assertEqual(D.sweep(LEAF, (0, 0), -2, {"w": wall}, step=0.5)["obstructions"], ["w"])      # turned the other way it moves into it


class Planning(unittest.TestCase):
    def setUp(self):
        self.p = Project.create("Door")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.door = self.p.execute("model.add_door", {"wall": self.wall, "offset": 2.0, "width": 900})["result"]["id"]

    def obstacle(self, y_mm):
        return self.p.execute("model.add_wall", {"start": [0, y_mm], "end": [4000, y_mm], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]

    def test_a_free_door_opens_fully_and_turns_toward_its_swing_side(self):
        r = D.plan(self.p, self.door, 90)
        self.assertEqual((r["obstructions"], r["clear_to_degrees"], r["turn_degrees"]), ([], r["turn_degrees"], 90.0))
        self.assertTrue(r["checked"])

    def test_an_obstruction_limits_the_opening_and_is_named(self):
        wall = self.obstacle(600)
        r = D.plan(self.p, self.door, 90)
        self.assertEqual(r["obstructions"], [wall])
        self.assertTrue(0 < abs(r["clear_to_degrees"]) < 45, r["clear_to_degrees"])
        self.assertGreater(r["clear_to_degrees"] * r["turn_degrees"], 0)               # same direction as the turn

    def test_swinging_the_other_way_is_not_blocked_by_that_wall(self):
        self.obstacle(600)
        self.p.execute("model.set_property", {"id": self.door, "key": "swing", "value": "right"})
        r = D.plan(self.p, self.door, 90)
        self.assertEqual((r["obstructions"], r["turn_degrees"]), ([], -90.0 if r["hinge"] == "start" else 90.0))

    def test_the_hinge_side_flips_the_turn(self):
        a = D.plan(self.p, self.door, 90, check=False)["turn_degrees"]
        self.p.execute("model.set_property", {"id": self.door, "key": "hinge", "value": "end"})
        self.assertEqual(D.plan(self.p, self.door, 90, check=False)["turn_degrees"], -a)

    def test_refusals(self):
        for bad in (0, -5, 181, float("nan"), True, "90"):
            with self.assertRaises(BadRequest, msg=bad):
                D.plan(self.p, self.door, bad)
        with self.assertRaises(BadRequest):
            D.plan(self.p, self.wall, 90)
        window = self.p.execute("model.add_window", {"wall": self.wall, "offset": 1.0, "width": 900})["result"]["id"]
        with self.assertRaises(BadRequest):
            D.plan(self.p, window, 90)

    def test_cladding_hard_against_the_jamb_is_cleared_by_a_leaf_that_turns_on_its_real_hinge(self):
        """The hinge pin is on the leaf's own swing-side face at the hinge edge, so the leaf turns away from the jamb. Facade panels hard against the jamb on the swing
        side are touched by well under a millimetre (the free edge's back corner), not entered by the 25 mm that a pin in the middle of the leaf's thickness would give."""
        self.p.execute("procedural.facade", {"wall": self.wall, "panel_width": 1000, "panel_height": 1000})
        r = D.plan(self.p, self.door, 90)
        self.assertEqual(r["obstructions"], [])
        deepest = max(r["contacts_mm"].values(), default=0.0)
        self.assertLess(deepest, 1.0, r["contacts_mm"])
        strict = D.plan(self.p, self.door, 90, tolerance_mm=0)
        self.assertEqual(strict["tolerance_mm"], 0.0)
        self.assertEqual(strict["contacts_mm"], r["contacts_mm"])                         # what is touched is listed whatever the tolerance
        for bad in (-1, 51, True, float("nan")):
            with self.assertRaises(BadRequest, msg=bad):
                D.plan(self.p, self.door, 90, tolerance_mm=bad)

    def test_the_command_refuses_an_obstructed_door_and_changes_nothing(self):
        self.obstacle(600)
        before = self.p.revision
        with self.assertRaises(Conflict) as cm:
            self.p.execute("anim.swing_door", {"id": self.door, "degrees": 90})
        self.assertIn("would hit", str(cm.exception))
        self.assertEqual(self.p.revision, before)


@unittest.skipUnless(AVAILABLE, SKIP)
class Evaluated(unittest.TestCase):
    def leaf_mean_y(self, p, door, t):
        verts = next(o for o in p.scene_data(time=t)["objects"] if o["id"] == door)["vertices"]
        return sum(v[1] for v in verts) / len(verts)

    def test_the_leaf_ends_on_its_swing_side_for_every_hinge_and_swing(self):
        for hinge in ("start", "end"):
            for swing, want in (("left", 1), ("right", -1)):
                p = Project.create("Door")
                level = p.manifest["levels"][0]["id"]
                wall = p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3})["result"]["id"]
                door = p.execute("model.add_door", {"wall": wall, "offset": 2.0, "width": 900})["result"]["id"]
                p.execute("model.set_property", {"id": door, "key": "hinge", "value": hinge})
                p.execute("model.set_property", {"id": door, "key": "swing", "value": swing})
                p.execute("anim.settings", {"duration": 2})
                p.execute("anim.swing_door", {"id": door, "degrees": 90, "start": 0, "end": 2})
                self.assertLess(abs(self.leaf_mean_y(p, door, 0)), 1e-6, (hinge, swing))
                self.assertGreater(self.leaf_mean_y(p, door, 2) * want, 0.3, (hinge, swing))     # opened onto the swing side, about 0.55 m from the wall line


if __name__ == "__main__":
    unittest.main()
