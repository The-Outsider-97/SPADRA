"""The agent hub and the Simulation, Spatial and Optimization adapters, run against the real agents and checked against closed-form results.
Skipped (never faked) where an agent is not importable."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import optimization_bridge as OB
from applications.SPADRA import simulation_bridge as SB
from applications.SPADRA.agent_hub import BY_KEY, HUB, SPECS
from applications.SPADRA.assistant import Assistant
from applications.SPADRA.project import Project
from applications.SPADRA.spatial_bridge import BRIDGE as SPATIAL
from applications.tools.errors import BadRequest

CASE = {"support": "simply_supported", "load_type": "udl", "load": 10, "youngs_modulus_gpa": 200, "density_kg_m3": 7850}


class Hub(unittest.TestCase):
    def test_every_agent_is_listed_with_a_reason_when_it_is_not_integrated(self):
        self.assertGreaterEqual(len(SPECS), 20)
        for spec in SPECS:
            self.assertTrue(spec.purpose or spec.why_not)
            if not spec.integrated:
                self.assertTrue(spec.why_not, f"{spec.key} is not integrated and says nothing about why")
        self.assertEqual({k for k, v in BY_KEY.items() if v.integrated}, {"stem", "simulation", "spatial", "optimization", "animation"})
        rows = HUB.status()
        self.assertEqual(len(rows), len(SPECS))
        for row in rows:
            if not row["integrated"]:
                self.assertFalse(row["available"])                    # an agent SPADRA does not call is never reported as in use


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Agents")
        self.level = self.p.manifest["levels"][0]["id"]
        self.beam = self.p.execute("model.add_beam", {"start": [0, 0], "end": [5000, 0], "level": self.level, "top": 3.0, "width": 200, "depth": 300, "material": "Steel"})["result"]["id"]


@unittest.skipUnless(SB.status()["available"], f"the Simulation and STEM agents are not importable here ({SB.status().get('reason')})")
class Simulation(Base):
    def test_trajectory_matches_the_closed_form_and_the_run_is_recorded(self):
        r = SB.simulate_beam(self.p, self.beam, {**CASE, "damping_ratio": 0.02})
        d, v = r["derived"], r["verification"]
        self.assertTrue(v["passed"])
        self.assertLess(v["relative_to_peak"], 1e-4)
        self.assertAlmostEqual(d["natural_frequency_rad_s"], math.sqrt(d["stiffness_n_per_m"] / d["effective_mass_kg"]), places=6)
        self.assertAlmostEqual(r["series"]["displacement_m"][0], d["static_deflection_m"])          # released from the static deflection
        self.assertEqual(r["provenance"]["solver"]["executor"], "SLAI Simulation Agent (rollout)")
        self.assertFalse(r["provenance"]["validity"]["validated_against_measurement"])
        self.assertEqual(self.p.revision, 1)                                                        # a simulation reads the project

    def test_step_load_peaks_near_twice_the_static_deflection_when_undamped(self):
        r = SB.simulate_beam(self.p, self.beam, {**CASE, "scenario": "step"})
        self.assertAlmostEqual(r["response"]["amplification"], 2.0, delta=0.07)

    def test_what_is_missing_is_asked_for_not_assumed(self):
        timber = self.p.execute("model.add_beam", {"start": [0, 1000], "end": [5000, 1000], "level": self.level, "top": 3.0, "width": 200, "depth": 300, "material": "Timber"})["result"]["id"]
        with self.assertRaises(BadRequest) as c:
            SB.simulate_beam(self.p, timber, {"support": "simply_supported", "load_type": "udl", "load": 10, "youngs_modulus_gpa": 10})     # timber: no density known
        self.assertIn("density", str(c.exception))
        wall = self.p.execute("model.add_wall", {"start": [0, 3000], "end": [4000, 3000], "level": self.level})["result"]["id"]
        with self.assertRaises(BadRequest):
            SB.simulate_beam(self.p, wall, CASE)

    def test_the_assistant_runs_it_and_lists_the_agent_calls(self):
        m = Assistant().ask(self.p, "Simulate the selected beam, simply supported, uniform load 10 kN/m, damping 2%, E 200 GPa, density 7850 kg/m3", {"selection": [self.beam]})
        self.assertEqual(m["kind"], "analysis")
        self.assertIn("preliminary", m["text"])
        self.assertIn("Simulation Agent", {c["agent"] for c in m["agents"]})
        needs = Assistant().ask(self.p, "Simulate the selected beam", {"selection": [self.beam]})
        self.assertEqual(needs["kind"], "failed")                                # no load case stated: asked for, not assumed


@unittest.skipUnless(OB.status()["available"], f"the Optimization and STEM agents are not importable here ({OB.status().get('reason')})")
class Optimization(Base):
    def test_the_optimum_is_the_analytic_one_and_applying_it_is_a_separate_approved_step(self):
        p = {**CASE, "variable": "depth", "lower_mm": 100, "upper_mm": 600, "deflection_ratio": 360}
        r = OB.optimize_beam(self.p, self.beam, p)
        self.assertTrue(r["found"])
        span, w, load, e = 5.0, 0.2, 10_000.0, 200e9
        limit = span / 360
        d_analytic = (12 * 5 * load * span ** 4 / (384 * e * w * limit)) ** (1 / 3)               # delta = 5wL^4 / (384 E b d^3 / 12)
        self.assertAlmostEqual(r["best"]["value_mm"] / 1000, d_analytic, delta=2e-4)
        self.assertTrue(r["best"]["check"]["meets_limit"])
        self.assertEqual(r["agent"]["name"], "SLAI Optimization Agent")
        self.assertEqual(self.p.revision, 1)                                                      # nothing was changed
        a = Assistant()
        m = a.ask(self.p, "Optimize the depth of the selected beam between 100 and 600 mm, simply supported, uniform load 10 kN/m, E 200 GPa", {"selection": [self.beam]})
        self.assertEqual(m["kind"], "proposal")
        self.assertEqual(self.p.revision, 1)
        a.approve(self.p, m["proposal"]["id"])
        depth = {x["key"]: x["value"] for x in self.p.properties({"model": self.beam})["groups"][0]["rows"] if x.get("key")}
        self.assertTrue(any(abs(v - r["change"]["value"]) < 1e-6 for v in depth.values()) or self.p.revision == 2)

    def test_bounds_are_the_persons(self):
        with self.assertRaises(BadRequest):
            OB.optimize_beam(self.p, self.beam, CASE)
        with self.assertRaises(BadRequest):
            OB.optimize_beam(self.p, self.beam, {**CASE, "lower_mm": 500, "upper_mm": 200})

    def test_an_infeasible_problem_is_reported_not_papered_over(self):
        r = OB.optimize_beam(self.p, self.beam, {**CASE, "load": 500, "lower_mm": 100, "upper_mm": 150})
        self.assertFalse(r["found"])


@unittest.skipUnless(SPATIAL.status()["available"], f"the Spatial Agent is not importable here ({SPATIAL.status().get('reason')})")
class Spatial(Base):
    def test_connected_and_nearby(self):
        w1 = self.p.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level})["result"]["id"]
        w2 = self.p.execute("model.add_wall", {"start": [4000, 0], "end": [4000, 3000], "level": self.level})["result"]["id"]
        far = self.p.execute("model.add_wall", {"start": [20000, 20000], "end": [24000, 20000], "level": self.level})["result"]["id"]
        near = SPATIAL.connected(self.p, w1)
        ids = {b["id"] for b in near["boxes"]}
        self.assertIn(w2, ids)
        self.assertNotIn(far, ids)
        self.assertEqual(near["agent"], "SLAI Spatial Agent")
        rows = SPATIAL.nearby(self.p, w1, 6.0)["elements"]
        self.assertIn(w2, {r["id"] for r in rows})
        self.assertNotIn(far, {r["id"] for r in rows})
        m = Assistant().ask(self.p, "Find all elements connected to this wall", {"selection": [w1]})
        self.assertEqual(m["kind"], "analysis")
        self.assertIn("Spatial Agent", {c["agent"] for c in m["agents"]})

    def test_a_door_is_connected_to_its_wall_by_the_model_itself(self):
        wall = self.p.execute("model.add_wall", {"start": [0, 3000], "end": [4000, 3000], "level": self.level})["result"]["id"]
        door = self.p.execute("model.add_door", {"wall": wall, "offset": 1.0})["result"]["id"]
        near = SPATIAL.connected(self.p, wall)
        self.assertIn(door, {s["id"] for s in near["stated_by_model"]})


if __name__ == "__main__":
    unittest.main()
