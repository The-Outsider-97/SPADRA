"""The engineering calculator: conversions, formulas and constraint checks are the SLAI STEM Agent's; measurements come from SGEO and the Spatial Agent.
Skipped (never faked) where the agents are not importable."""
from __future__ import annotations

import unittest

from applications.SPADRA import calculator as C
from applications.SPADRA.engineering_bridge import BRIDGE as STEM
from applications.SPADRA.project import Project
from applications.SPADRA.spatial_bridge import BRIDGE as SPATIAL
from applications.tools.errors import BadRequest


@unittest.skipUnless(STEM.available, f"the SLAI STEM Agent is not importable here ({STEM.reason})")
class Calculator(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Calc")
        self.level = self.p.manifest["levels"][0]["id"]
        self.wall = self.p.execute("model.add_wall", {"start": [0, 0], "end": [6000, 0], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        self.room = self.p.execute("model.add_room", {"level": self.level, "points": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]})["result"]["id"]

    def test_conversion_is_the_agents(self):
        r = C.convert(self.p, {"value": 2.5, "from": "m", "to": "mm"})
        self.assertEqual(r["result"], {"value": 2500.0, "unit": "mm"})
        self.assertEqual(r["agent"], "SLAI STEM Agent")
        self.assertEqual(r["revision"], self.p.revision)
        self.assertAlmostEqual(C.convert(None, {"value": 1, "from": "GPa", "to": "MPa"})["result"]["value"], 1000.0)

    def test_incompatible_and_unknown_units_are_refused_with_the_engines_message(self):
        with self.assertRaises(BadRequest) as c:
            C.convert(self.p, {"value": 1, "from": "m", "to": "kg"})
        self.assertIn("dimensionally", str(c.exception))
        with self.assertRaises(BadRequest):
            C.convert(self.p, {"value": 1, "from": "furlong", "to": "m"})

    def test_formulas_over_the_elements_own_parameters(self):
        ep = C.element_parameters(self.p, [self.wall])
        self.assertEqual(ep["parameters"]["e1_height"], {"value": 3.0, "unit": "m"})
        self.assertEqual(ep["parameters"]["e1_thickness"], {"value": 200.0, "unit": "mm"})
        r = C.evaluate(self.p, {"formulas": {"volume": "e1_length*e1_height*e1_thickness"}, "parameters": ep["parameters"]})
        v = r["results"]["volume"]
        self.assertAlmostEqual(v["si_value"], 3.6)                              # 6 m x 3 m x 0.2 m, units reconciled by the engine
        self.assertEqual(v["dimension"], {"L": 3.0})
        self.assertAlmostEqual(v["si_value"], self.p.quantities([self.wall], None)["rows"][0]["volume_m3"])

    def test_constraints_check_dimensions_and_report_each_result(self):
        ep = C.element_parameters(self.p, [self.wall])
        r = C.check_constraints(self.p, {"parameters": ep["parameters"], "constraints": [
            {"left": "e1_height", "op": "<=", "right": {"value": 4, "unit": "m"}}, {"left": "e1_thickness", "op": ">=", "right": {"value": 250, "unit": "mm"}}]})
        self.assertFalse(r["feasible"])
        self.assertEqual([x["passed"] for x in r["results"]], [True, False])
        with self.assertRaises(BadRequest):
            C.check_constraints(self.p, {"parameters": ep["parameters"], "constraints": ["e1_height <= e1_thickness*e1_thickness*e1_thickness/e1_thickness/e1_thickness/e1_height*e1_height*e1_height"]})

    def test_garbage_input_is_refused(self):
        with self.assertRaises(BadRequest):
            C.evaluate(self.p, {"formulas": {"x": "__import__('os').system('echo hi')"}, "parameters": {}})
        with self.assertRaises(BadRequest):
            C.evaluate(self.p, {"formulas": {}, "parameters": {}})
        with self.assertRaises(BadRequest):
            C.element_parameters(self.p, [])

    def test_measure_one_element_and_a_room(self):
        w = {f["label"]: f for f in C.measure(self.p, [self.wall])["figures"]}
        self.assertAlmostEqual(w["Volume"]["value"], 3.6)
        room = {f["label"]: f["value"] for f in C.measure(self.p, [self.room])["figures"]}
        self.assertAlmostEqual(room["Area"], 24.0)
        self.assertAlmostEqual(room["Perimeter"], 20.0)

    @unittest.skipUnless(SPATIAL.status()["available"], "the SLAI Spatial Agent is not importable here")
    def test_distance_between_two_elements_is_the_spatial_agents(self):
        other = self.p.execute("model.add_wall", {"start": [0, 5000], "end": [6000, 5000], "level": self.level, "thickness": 200, "height": 3})["result"]["id"]
        m = C.measure(self.p, [self.wall, other])
        figs = {f["label"]: f["value"] for f in m["figures"]}
        self.assertAlmostEqual(figs["Distance between centres"], 5.0)
        self.assertAlmostEqual(figs["Gap between bounding boxes"], 4.8)
        self.assertFalse(figs["Bounding boxes collide"])
        self.assertEqual(m["agent"], "SLAI Spatial Agent")
        with self.assertRaises(BadRequest):
            C.measure(self.p, [self.wall, self.room])                           # a room has no solid geometry
        with self.assertRaises(BadRequest):
            C.measure(self.p, [self.wall, other, self.wall])


if __name__ == "__main__":
    unittest.main()
