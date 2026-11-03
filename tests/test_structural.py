"""Beam analysis through the real SLAI STEM Agent, checked against textbook closed-form results. Skipped (not faked) where the SLAI checkout
has no ``src/agents``; run from a SLAI root that contains it, e.g. ``python -m unittest discover -s applications/SPADRA/tests -t .``"""
from __future__ import annotations

import unittest

from applications.SPADRA import engineering_bridge as EB
from applications.SPADRA.project import Project
from applications.tools.errors import BadRequest

AVAILABLE = EB.BRIDGE.available
SKIP = f"the SLAI STEM Agent is not importable here ({EB.BRIDGE.reason})"

B, H, L = 0.2, 0.4, 6.0                          # section 200 x 400 mm, span 6 m
I = B * H ** 3 / 12
E_STEEL = 200e9                                  # the engine's structural_steel_a36


@unittest.skipUnless(AVAILABLE, SKIP)
class Beams(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Beams")
        level = self.p.manifest["levels"][0]["id"]
        self.beam = self.p.execute("model.add_beam", {"start": [0, 0], "end": [L * 1000, 0], "level": level, "top": 3.0, "width": 200, "depth": 400, "material": "Steel"})["result"]["id"]
        self.timber = self.p.execute("model.add_beam", {"start": [0, 1000], "end": [L * 1000, 1000], "level": level, "top": 3.0, "width": 200, "depth": 400, "material": "Timber"})["result"]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 3000], "end": [4000, 3000], "level": level, "thickness": 200, "height": 3})["result"]["id"]

    def run_case(self, support, load_type, load_kn, oid=None, **extra):
        return self.p.analyze_beam(oid or self.beam, {"support": support, "load_type": load_type, "load": load_kn, **extra})

    def assertClose(self, got, want):
        self.assertAlmostEqual(got / want, 1.0, places=9)

    # ------------------------------------------------------------------------------------------------------------------ the agent
    def test_the_agent_is_the_real_stem_agent(self):
        self.assertEqual(EB.BRIDGE._agent.__class__.__module__, "src.agents.stem_agent")
        status = EB.BRIDGE.status()
        self.assertTrue(status["available"])
        self.assertIn({"support": "simply_supported", "load_type": "udl"}, status["cases"])

    # ------------------------------------------------------------------------------------------------------------------ closed form
    def test_simply_supported_uniform_load(self):
        w = 10_000.0
        r = self.run_case("simply_supported", "udl", 10)
        self.assertClose(r["outputs"]["max_moment_nm"]["value"], w * L ** 2 / 8)
        self.assertClose(r["outputs"]["max_shear_n"]["value"], w * L / 2)
        self.assertClose(r["outputs"]["max_deflection_m"]["value"], 5 * w * L ** 4 / (384 * E_STEEL * I))
        self.assertEqual(r["outputs"]["max_moment_nm"]["unit"], "N*m")
        self.assertEqual(r["agent"], "SLAI STEM Agent")

    def test_simply_supported_centre_point_load(self):
        P = 50_000.0
        r = self.run_case("simply_supported", "point_center", 50)
        self.assertClose(r["outputs"]["max_moment_nm"]["value"], P * L / 4)
        self.assertClose(r["outputs"]["max_shear_n"]["value"], P / 2)
        self.assertClose(r["outputs"]["max_deflection_m"]["value"], P * L ** 3 / (48 * E_STEEL * I))

    def test_cantilever_cases(self):
        w, P = 10_000.0, 20_000.0
        u = self.run_case("cantilever", "udl", 10)
        self.assertClose(u["outputs"]["max_moment_nm"]["value"], w * L ** 2 / 2)
        self.assertClose(u["outputs"]["max_deflection_m"]["value"], w * L ** 4 / (8 * E_STEEL * I))
        e = self.run_case("cantilever", "point_end", 20)
        self.assertClose(e["outputs"]["max_moment_nm"]["value"], P * L)
        self.assertClose(e["outputs"]["max_shear_n"]["value"], P)
        self.assertClose(e["outputs"]["max_deflection_m"]["value"], P * L ** 3 / (3 * E_STEEL * I))

    def test_fixed_fixed_uniform_load(self):
        w = 10_000.0
        r = self.run_case("fixed_fixed", "udl", 10)
        self.assertClose(r["outputs"]["max_moment_nm"]["value"], w * L ** 2 / 12)
        self.assertClose(r["outputs"]["max_deflection_m"]["value"], w * L ** 4 / (384 * E_STEEL * I))

    # ------------------------------------------------------------------------------------------------------------------ inputs come from the beam
    def test_the_inputs_used_are_reported_and_come_from_the_beam(self):
        r = self.run_case("simply_supported", "udl", 10)
        i = r["inputs"]
        self.assertAlmostEqual(i["span_m"], L, places=9)
        self.assertAlmostEqual(i["second_moment_m4"], I, places=15)
        self.assertAlmostEqual(i["youngs_modulus_gpa"], 200.0)
        self.assertIn("structural_steel_a36", i["youngs_modulus_source"])
        self.assertEqual((i["load"], i["load_unit"]), (10.0, "kN/m"))
        self.assertIn("self-weight not included", r["assumptions"])
        self.assertIn("linear elasticity", r["assumptions"])                      # the agent's own assumption, passed through
        self.assertIn("not a structural design", r["note"])

    def test_a_deeper_beam_deflects_less_in_the_cube_of_the_depth(self):
        shallow = self.run_case("simply_supported", "udl", 10)["outputs"]["max_deflection_m"]["value"]
        self.p.execute("model.set_property", {"id": self.beam, "key": "depth", "value": 800})
        deep = self.run_case("simply_supported", "udl", 10)["outputs"]["max_deflection_m"]["value"]
        self.assertClose(shallow / deep, 8.0)

    def test_the_engines_own_deflection_check_is_returned(self):
        r = self.run_case("simply_supported", "udl", 10)
        self.assertIn("deflection_limit_m", r["outputs"])
        self.assertIn("base_engineering_check", r["details"])

    # ------------------------------------------------------------------------------------------------------------------ nothing is invented
    def test_a_material_the_engine_has_no_modulus_for_needs_one_from_the_person(self):
        with self.assertRaisesRegex(BadRequest, "SPADRA does not assume one"):
            self.run_case("simply_supported", "udl", 10, oid=self.timber)
        r = self.run_case("simply_supported", "udl", 10, oid=self.timber, youngs_modulus_gpa=11)
        self.assertIn("entered by you", r["inputs"]["youngs_modulus_source"])
        self.assertClose(r["outputs"]["max_deflection_m"]["value"], 5 * 10_000.0 * L ** 4 / (384 * 11e9 * I))

    def test_an_entered_modulus_overrides_the_engines(self):
        r = self.run_case("simply_supported", "udl", 10, youngs_modulus_gpa=100)
        self.assertClose(r["outputs"]["max_deflection_m"]["value"], 5 * 10_000.0 * L ** 4 / (384 * 100e9 * I))

    def test_a_missing_or_bad_load_is_refused(self):
        with self.assertRaisesRegex(BadRequest, "Enter the load"):
            self.p.analyze_beam(self.beam, {"support": "simply_supported", "load_type": "udl"})
        for bad in (0, -5, "abc", float("nan")):
            with self.assertRaises(BadRequest):
                self.run_case("simply_supported", "udl", bad)

    def test_unsupported_combinations_and_other_elements_are_refused(self):
        with self.assertRaisesRegex(BadRequest, "does not support"):
            self.run_case("cantilever", "point_center", 10)
        with self.assertRaises(BadRequest):
            self.run_case("pinned", "udl", 10)
        with self.assertRaisesRegex(BadRequest, "beams only"):
            self.run_case("simply_supported", "udl", 10, oid=self.wall)

    def test_analysis_is_read_only(self):
        before = (self.p.revision, self.p.dirty, self.p.scene_data())
        self.run_case("simply_supported", "udl", 10)
        self.assertEqual((self.p.revision, self.p.dirty, self.p.scene_data()), before)


class Unavailable(unittest.TestCase):
    def test_the_bridge_reports_why_when_the_agent_cannot_load(self):
        bridge = EB.EngineeringBridge()
        bridge._tried, bridge._reason = True, "ImportError: no module named src.agents"
        self.assertFalse(bridge.available)
        self.assertIn("src.agents", bridge.status()["reason"])
        from applications.tools.errors import DependencyMissing
        with self.assertRaises(DependencyMissing):
            bridge.beam_response(span_m=1, load=1, load_type="udl", support="simply_supported", second_moment_m4=1, youngs_modulus_pa=1)


if __name__ == "__main__":
    unittest.main()
