"""The agent hub and the Simulation, Spatial and Optimization adapters, run against the real agents and checked against closed-form results.
Skipped (never faked) where an agent is not importable."""
from __future__ import annotations

import math
import re
import types
import unittest
from pathlib import Path
from unittest import mock

from applications.SPADRA import animation_bridge as AB
from applications.SPADRA import engineering_bridge as EB
from applications.SPADRA import optimization_bridge as OB
from applications.SPADRA import simulation_bridge as SB
from applications.SPADRA import spatial_bridge as SPB
from applications.SPADRA.agent_hub import BY_KEY, HUB, SPECS, roles
from applications.SPADRA.assistant import Assistant
from applications.SPADRA.project import Project
from applications.SPADRA.spatial_bridge import BRIDGE as SPATIAL
from applications.tools.agents import AgentHub
from applications.tools.agents import AgentSpec as SharedSpec
from applications.tools.commit_host import ADAPTER_MODULE
from applications.tools.errors import BadRequest

CASE = {"support": "simply_supported", "load_type": "udl", "load": 10, "youngs_modulus_gpa": 200, "density_kg_m3": 7850}


class Hub(unittest.TestCase):
    def test_every_agent_is_listed_with_a_reason_when_it_is_not_integrated(self):
        self.assertGreaterEqual(len(SPECS), 20)
        for spec in SPECS:
            self.assertTrue(spec.purpose or spec.why_not)
            if not spec.integrated:
                self.assertTrue(spec.why_not, f"{spec.key} is not integrated and says nothing about why")
        self.assertEqual({k for k, v in BY_KEY.items() if v.integrated}, {"stem", "simulation", "spatial", "optimization", "animation", "observability"})
        rows = HUB.status()
        self.assertEqual(len(rows), len(SPECS))
        for row in rows:
            if not row["integrated"]:
                self.assertFalse(row["available"])                    # an agent SPADRA does not call is never reported as in use


# What each reason says is checked against its source below, so a sentence copied from the machine SPADRA was built on cannot sit here and go stale.
ENVIRONMENT_CLAIMS = ("where SPADRA was built", "could not be imported", "not importable", "ModuleNotFoundError", "did not import")
APPLICATIONS = Path(__file__).resolve().parents[1]


class Memory:
    pass


class Fault(Exception):
    __module__ = "src.agents.fake"                       # how a bridge recognises an agent's own refusal


class StandInStem:
    def __init__(self, memory=None, factory=None) -> None:
        self.engineering = types.SimpleNamespace(civil=types.SimpleNamespace(BEAM_CASES=[("simply_supported", "udl")]))


class StandInAnimation:
    def __init__(self, memory=None, factory=None) -> None:
        self.memory = memory


class StandInSpatial:
    def __init__(self, memory=None, factory=None) -> None:
        pass

    def perform_task(self, payload):
        op = payload["operation"]
        if op == "refuse":
            raise Fault("not allowed")
        if op == "crash":
            raise RuntimeError("server fault")
        if op == "disabled":
            return {"status": "disabled", "result": None}
        return {"status": "ok", "request_id": "r1", "result": {"echo": op}}


def stand_in_hub(**classes) -> AgentHub:
    """The shared hub over stand-in agent modules (``key=class``; ``key=None`` is an agent whose import fails)."""
    specs = [SharedSpec(key, f"{key.title()} Agent", f"{key}_agent", f"{key.title()}Agent") for key in classes]

    def importer(name: str):
        if name == "src.agents.collaborative.shared_memory":
            return types.SimpleNamespace(SharedMemory=Memory)
        for key, cls in classes.items():
            if name == f"src.agents.{key}_agent":
                if cls is None:
                    raise ModuleNotFoundError("No module named 'torch'")
                return types.SimpleNamespace(**{f"{key.title()}Agent": cls})
        raise ImportError(f"No module named {name!r}")

    return AgentHub(specs, importer=importer)


class HubStructure(unittest.TestCase):
    def test_the_hub_is_the_shared_hub_not_a_second_copy_of_it(self):
        self.assertIsInstance(HUB, AgentHub)
        own = {name for name in vars(type(HUB)) if not name.startswith("__")}
        self.assertTrue({"load", "require", "available", "reason", "record", "present"}.isdisjoint(own), "SPADRA's hub re-implements what the shared hub already does")

    def test_no_reason_states_what_imports_on_some_other_computer(self):
        for spec in SPECS:
            for phrase in ENVIRONMENT_CLAIMS:
                self.assertNotIn(phrase, spec.why_not, f"{spec.key}: {phrase!r} describes one computer, not the agent")
        for role in roles():
            for phrase in ENVIRONMENT_CLAIMS:
                self.assertNotIn(phrase, role["used_for"], role["role"])

    def test_a_role_gives_its_agents_reason_once_and_does_not_restate_it(self):
        for role in roles():
            if role["agent_calls"]:
                self.assertNotIn("why_not", role, role["role"])
            else:
                self.assertEqual(role["why_not"], BY_KEY[role["agent"]].why_not, role["role"])
                self.assertNotIn(role["why_not"], role["used_for"], role["role"])

    def test_the_torch_claims_are_the_ones_slai_itself_makes(self):
        try:
            from src.agents.agent_factory import AgentFactory
            profiles = AgentFactory.DEFAULT_DEPENDENCY_PROFILES
        except Exception as exc:                                            # noqa: BLE001
            self.skipTest(f"SLAI's agent factory is not importable here ({type(exc).__name__}: {exc})")
        for spec in SPECS:
            if "PyTorch" in spec.why_not:
                self.assertTrue(profiles[spec.key]["torch_required"], f"{spec.key}: SLAI's own table does not say it needs PyTorch")

    def test_planning_names_the_commit_adapters_spadra_really_uses(self):
        self.assertEqual(ADAPTER_MODULE, "src.agents.planning.project_adapter")
        why = BY_KEY["planning"].why_not
        for name in ("project_adapter.py", "commit_reconciliation.py", "workflow_runtime.py"):
            self.assertIn(name, why)
        commits = (APPLICATIONS / "commits.py").read_text(encoding="utf-8")
        host = (APPLICATIONS.parent / "tools" / "commit_host.py").read_text(encoding="utf-8")
        self.assertIn("planning_types", commits)                            # SPADRA's commit path uses the adapters ...
        self.assertIn("src.agents.planning.commit_reconciliation", host)    # ... and the Outbox it is built on
        self.assertIn("src.agents.planning.workflow_runtime", host)

    def test_governance_is_not_used_because_spadra_never_goes_through_the_factory_or_execute(self):
        """The reason given for the Governance Agent holds only while SPADRA builds agents itself and calls ``perform_task``."""
        pattern = re.compile(r"(import[^\n]*agent_factory|AgentFactory\s*\(|\bagent\.execute\(|\bsim\.execute\()")
        for path in sorted(APPLICATIONS.glob("*.py")):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), f"{path.name} uses AgentFactory or an agent's execute(): update the Governance Agent's reason")
        self.assertFalse(BY_KEY["governance"].integrated)

    def test_the_hub_builds_stem_and_animation_the_way_their_bridges_always_did(self):
        from applications.SPADRA import agent_hub as AH
        made = []

        class Stem:
            def __init__(self, memory, factory) -> None:
                made.append(("stem", type(memory).__name__, factory))

        class Animation:
            def __init__(self, *args, **kwargs) -> None:
                made.append(("animation", args, kwargs))

        with mock.patch.object(AH.importlib, "import_module", return_value=types.SimpleNamespace(SharedMemory=Memory)):
            AH._build_stem(types.SimpleNamespace(STEMAgent=Stem))
        AH._build_animation(types.SimpleNamespace(AnimationAgent=Animation))
        self.assertEqual(made, [("stem", "Memory", None), ("animation", (), {})])      # STEM with a shared memory of its own; Animation with none

    def test_the_engineering_bridge_holds_the_hubs_stem_agent(self):
        hub = stand_in_hub(stem=StandInStem)
        with mock.patch.object(EB, "HUB", hub):
            bridge = EB.EngineeringBridge()
            self.assertTrue(bridge.available)
            self.assertIs(bridge._agent, hub.load("stem"))                  # one agent, not one for the hub and one for the bridge

    def test_the_engineering_bridge_reports_the_hubs_reason(self):
        hub = stand_in_hub(stem=None)
        with mock.patch.object(EB, "HUB", hub):
            bridge = EB.EngineeringBridge()
            self.assertFalse(bridge.available)
            self.assertEqual(bridge.reason, hub.reason("stem"))
            self.assertIn("ModuleNotFoundError", bridge.reason)
            with self.assertRaises(Exception) as caught:
                bridge.task("parametric", "evaluate_serializable")
            self.assertEqual(type(caught.exception).__name__, "DependencyMissing")

    def test_the_engineering_bridge_refuses_an_engine_that_is_not_the_one_it_expects(self):
        class Odd:
            def __init__(self, memory=None, factory=None) -> None:
                self.engineering = types.SimpleNamespace(civil=types.SimpleNamespace())          # no BEAM_CASES

        hub = stand_in_hub(stem=Odd)
        with mock.patch.object(EB, "HUB", hub):
            bridge = EB.EngineeringBridge()
            self.assertFalse(bridge.available)
            self.assertIn("AttributeError", bridge.reason)

    def test_the_animation_bridge_holds_the_hubs_animation_agent(self):
        hub = stand_in_hub(animation=StandInAnimation)
        with mock.patch.object(AB, "HUB", hub):
            bridge = AB.AnimationBridge()
            self.assertTrue(bridge.available)
            self.assertIs(bridge._agent, hub.load("animation"))
            self.assertIsNone(bridge.reason)
        broken = stand_in_hub(animation=None)
        with mock.patch.object(AB, "HUB", broken):
            bridge = AB.AnimationBridge()
            self.assertFalse(bridge.available)
            self.assertEqual(bridge.reason, broken.reason("animation"))

    def test_a_spatial_call_is_recorded_once_with_its_real_outcome(self):
        cases = [("bounds", None, True, "r1"), ("disabled", BadRequest, False, None), ("refuse", BadRequest, False, None), ("crash", RuntimeError, False, None)]
        for operation, error, ok, request_id in cases:
            hub = stand_in_hub(spatial=StandInSpatial)
            with mock.patch.object(SPB, "HUB", hub):
                bridge = SPB.SpatialBridge()
                if error is None:
                    self.assertEqual(bridge._task({"operation": operation}), {"echo": operation})
                else:
                    with self.assertRaises(error):
                        bridge._task({"operation": operation})
            self.assertEqual(len(hub.calls), 1, operation)                  # one record per call, never two and never none
            row = hub.calls[0]
            self.assertEqual((row["agent"], row["operation"], row["ok"], row["request_id"]), ("Spatial Agent", operation, ok, request_id), operation)
            self.assertIn("ms", row)

    def test_a_beam_analysis_is_listed_as_the_stem_call_it_is(self):
        from applications.SPADRA import structural as ST
        hub = stand_in_hub(stem=StandInStem)
        project = Project.create("Beam call")
        level = project.manifest["levels"][0]["id"]
        beam = project.execute("model.add_beam", {"start": [0, 0], "end": [5000, 0], "level": level, "top": 3.0, "width": 200, "depth": 300, "material": "Steel"})["result"]["id"]
        answer = {"method": "m", "outputs": {}, "assumptions": [], "details": {}, "validation": None, "request_id": "stem-1"}
        section = {"width_m": 0.2, "depth_m": 0.3, "profile": "rectangle", "source": "stand-in", "area_m2": 0.06, "second_moment_m4": 4.5e-4, "section_modulus_m3": 3e-3}
        with mock.patch.object(ST, "HUB", hub), mock.patch.object(ST, "section_of", return_value=section), \
                mock.patch.object(ST, "BRIDGE", mock.MagicMock(available=False, beam_response=mock.MagicMock(return_value=answer))):
            ST.analyze_beam(project, beam, {"support": "simply_supported", "load_type": "udl", "load": 10, "youngs_modulus_gpa": 200})
        self.assertEqual([(c["agent"], c["operation"], c["request_id"], c["ok"]) for c in hub.calls], [("STEM Agent", "engineering_analysis.beam_response", "stem-1", True)])

    def test_an_agents_own_refusal_keeps_its_name_for_the_person(self):
        hub = stand_in_hub(spatial=StandInSpatial)
        with mock.patch.object(SPB, "HUB", hub):
            with self.assertRaises(BadRequest) as caught:
                SPB.SpatialBridge()._task({"operation": "refuse"})
        self.assertEqual(caught.exception.details["error"], "Fault")


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

    def test_the_result_is_labelled_as_an_idealized_model(self):
        """Report 10.2: where the Simulation Agent returns only an idealized dynamic model, the application says so, beside the assumptions it rests on."""
        r = SB.simulate_beam(self.p, self.beam, CASE)
        self.assertIn("Idealized", r["idealization"]["label"])
        self.assertIn("lumped mass", r["idealization"]["meaning"])
        self.assertTrue(any("single degree of freedom" in a for a in r["provenance"]["assumptions"]))
        self.assertFalse(r["provenance"]["validity"]["structural_certification"])

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
