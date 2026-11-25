"""Layered validation (``validation_layers``): detection against labelled elements (valid, invalid and borderline), determinism, the independent volume
figure, the optional physical layer's refusal to invent numbers, and corrections that are only ever reviewable proposals bound to a revision.

The labels are the ground truth written down *before* the check runs: each case says which (layer, rule) pair must be reported for its element, or that
nothing may be. ``Accuracy.test_detection_accuracy_false_rejections_determinism_and_time`` prints the measured figures."""
from __future__ import annotations

import json
import time
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import validation_layers as VL
from applications.SPADRA.assistant import ASSISTANT
from applications.tools.errors import BadRequest, Conflict

from .test_bim import Case

FAR = 30_000_000                                                    # mm: a model 30 km from the origin (a geo-referenced site)


class Labelled(Case):
    """Twenty-odd elements spread so that they neither touch nor duplicate one another; ``self.cases`` is ``{name: (element id, expected)}`` where
    ``expected`` is ``None`` (nothing may be reported for the element) or a set of acceptable ``(layer, rule)`` pairs (at least one must be)."""

    def setUp(self) -> None:
        super().setUp()
        self.cases: dict[str, tuple[str, set | None]] = {}
        y = [0]

        def lane():
            y[0] += 8000
            return y[0]

        def wall(name, thickness=250, height=3.2, expected=None, x0=0, dx=6000, **kw):
            v = lane()
            oid = self.run_("model.add_wall", start=[x0, v], end=[x0 + dx, v], level=self.l1, thickness=thickness, height=height, **kw)["id"]
            self.cases[name] = (oid, expected)
            return oid, v

        wall("wall", )
        wall("wall at exactly the minimum thickness (20 mm)", thickness=20)
        wall("wall just above the minimum (21 mm)", thickness=21)
        wall("tall slender wall", thickness=100, height=12.0)
        wall("wall 30 km from the origin", x0=FAR, thickness=250)
        w, v = wall("wall with a door")
        self.run_("model.add_door", wall=w, offset=3.0, width=900, height=2100)
        w, v = wall("wall with a window")
        self.run_("model.add_window", wall=w, offset=3.0, width=1200, height=1200, sill=900)
        self.cases["floor"] = (self.run_("model.add_floor", level=self.l1, points=[[-9000, -9000], [-3000, -9000], [-3000, -3000], [-9000, -3000]], thickness=200)["id"], None)
        self.cases["floor on the upper level"] = (self.run_("model.add_floor", level=self.l2, points=[[-9000, 40000], [-3000, 40000], [-3000, 46000], [-9000, 46000]], thickness=250)["id"], None)
        self.cases["roof"] = (self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[-20000, -9000], [-14000, -9000], [-14000, -3000], [-20000, -3000]], thickness=100)["id"], None)
        self.cases["column"] = (self.run_("model.add_column", point=[-30000, -30000], level=self.l1)["id"], None)
        self.cases["beam"] = (self.run_("model.add_beam", start=[-40000, -40000], end=[-36000, -40000], level=self.l1)["id"], None)
        # invalid and borderline-invalid, by the API
        wall("wall just below the minimum (19 mm)", thickness=19, expected={(1, "min_thickness")})
        wall("wall 10 mm", thickness=10, expected={(1, "min_thickness")})
        self.cases["floor 10 mm"] = (self.run_("model.add_floor", level=self.l1, points=[[-9000, 20000], [-3000, 20000], [-3000, 26000], [-9000, 26000]], thickness=10)["id"], {(1, "min_thickness")})
        self.cases["roof 10 mm"] = (self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[-20000, 20000], [-14000, 20000], [-14000, 26000], [-20000, 26000]], thickness=10)["id"], {(1, "min_thickness")})
        # invalid, by data that a damaged or foreign file can carry (the API refuses to make these)
        w, v = wall("wall whose solid lost a face (open shell)", expected={(1, "solid_closed")})
        self.p.model.set_geometry(w, {"kind": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
                                      "faces": [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6]]})
        w, v = wall("wall whose solid is turned inside out", expected={(1, "solid_volume")})
        self.p.model.set_geometry(w, {"kind": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
                                      "faces": [[a, c, b] for a, b, c in [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]]})
        w, v = wall("door whose host is gone")
        d = self.run_("model.add_door", wall=w, offset=3.0, width=900, height=2100)["id"]
        self.cases["door whose host is gone"] = (d, {(2, "hosting"), (2, "references")})
        E._attach(self.p, d, {"host": "no-such-wall"})
        w, v = wall("wall on a level that does not exist", expected={(2, "references")})
        E._attach(self.p, w, {"level": "no-such-level"})
        w, v = wall("a second wall exactly on top of the first", expected={(2, "consistency")})
        first = self.cases["wall"][0]
        meta = self.meta(first)
        dup = self.run_("model.add_wall", start=[c * 1000 for c in meta["spadra:start"]], end=[c * 1000 for c in meta["spadra:end"]], level=self.l1, thickness=250, height=3.2)["id"]
        self.cases["a second wall exactly on top of the first"] = (dup, {(2, "consistency")})

    def reported(self, result, oid):
        return {(f["layer"], f["rule"]) for f in result["findings"] if f["ref"] == oid}


class Accuracy(Labelled):
    def test_detection_accuracy_false_rejections_determinism_and_time(self):
        started = time.perf_counter()
        first = VL.PIPELINE.validate(self.p)
        cold_ms = (time.perf_counter() - started) * 1000
        VL.PIPELINE._cache.clear()
        second = VL.PIPELINE.validate(self.p)
        strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("elapsed_ms", "cached")}, sort_keys=True)          # noqa: E731
        self.assertEqual(strip(first), strip(second), "two runs on the same revision must be identical")
        self.assertGreaterEqual(len(self.cases), 20)
        correct = false_rejections = misses = 0
        valid = invalid = 0
        for name, (oid, expected) in self.cases.items():
            got = self.reported(first, oid)
            if name == "a second wall exactly on top of the first":
                got |= self.reported(first, self.cases["wall"][0])                          # the duplicate finding names the pair; either member carries it
            errors = {(f["layer"], f["rule"]) for f in first["findings"] if f["ref"] == oid and f["severity"] == "error"}
            if expected is None:
                valid += 1
                if errors:
                    false_rejections += 1
                self.assertFalse(errors, f"valid element rejected: {name}: {errors}")
                correct += not errors
            else:
                invalid += 1
                hit = bool(got & expected)
                misses += not hit
                correct += hit
                self.assertTrue(hit, f"not detected: {name}: expected one of {expected}, got {got}")
        n = len(self.cases)
        print(f"\n[validation_layers] {n} labelled elements ({valid} valid, {invalid} invalid/borderline): detection {correct}/{n}, missed {misses}, "
              f"false rejections {false_rejections}/{valid}; cold run {cold_ms:.1f} ms, cached rerun {VL.PIPELINE.validate(self.p)['elapsed_ms']:.2f} ms")
        self.assertEqual((misses, false_rejections), (0, 0))

    def test_the_far_wall_is_measured_correctly_not_flagged_by_rounding(self):
        oid = self.cases["wall 30 km from the origin"][0]
        mesh = self.p.model.tessellate(oid)
        m = VL.solid_metrics(mesh["vertices"], mesh["faces"])
        self.assertAlmostEqual(m["volume"], 6.0 * 0.25 * 3.2, places=6)                       # summed from the mesh's own vertex, so 30 km away is as good as at the origin
        self.assertEqual((m["open_edges"], m["nonmanifold_edges"]), (0, 0))

    def test_the_independent_volume_matches_the_kernel_for_a_wall_with_an_opening(self):
        oid = self.cases["wall with a door"][0]
        mesh = self.p.model.tessellate(oid)
        m = VL.solid_metrics(mesh["vertices"], mesh["faces"])
        self.assertAlmostEqual(m["volume"], self.vol(oid), places=9)
        self.assertAlmostEqual(m["volume"], 6.0 * 0.25 * 3.2 - 0.9 * 2.1 * 0.25, places=9)

    def test_an_open_solid_is_reported_with_its_measured_edges(self):
        v = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]]
        faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6]]       # the x = 0 face is missing
        m = VL.solid_metrics(v, faces)
        self.assertEqual(m["open_edges"], 4)
        flipped = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
        self.assertEqual(VL.solid_metrics(v, flipped)["open_edges"], 0)
        reversed_ = [[a, c, b] for a, b, c in flipped]
        self.assertLess(VL.solid_metrics(v, reversed_)["volume"], 0)                           # turned inside out: negative volume, not a valid solid
        bad = [list(f) for f in flipped]
        bad[-1] = [3, 7, 4]                                                                    # one triangle against the winding of its neighbours
        self.assertGreater(VL.solid_metrics(v, bad)["nonmanifold_edges"], 0)
        self.assertFalse(VL.solid_metrics([[0, 0, float("nan")]] + v[1:], flipped)["finite"])


class Limits(Labelled):
    def test_the_minimum_is_the_callers_and_the_boundary_is_inclusive(self):
        normal = self.cases["wall"][0]
        at_limit = self.cases["wall at exactly the minimum thickness (20 mm)"][0]
        r = VL.PIPELINE.validate(self.p, limits={"min_thickness_m": 0.05})
        self.assertIn((1, "min_thickness"), self.reported(r, at_limit))
        self.assertNotIn((1, "min_thickness"), self.reported(r, normal))
        self.assertEqual(r["limits"]["min_thickness_m"], 0.05)
        thin = VL.PIPELINE.validate(self.p, ids=[at_limit])
        self.assertEqual(self.reported(thin, at_limit), set())
        self.assertEqual(thin["scope"], [at_limit])

    def test_unknown_or_absurd_limits_are_refused(self):
        with self.assertRaises(BadRequest):
            VL.PIPELINE.validate(self.p, limits={"min_thick": 0.1})
        with self.assertRaises(BadRequest):
            VL.PIPELINE.validate(self.p, limits={"min_thickness_m": 100})
        with self.assertRaises(BadRequest):
            VL.PIPELINE.validate(self.p, layers=[4])

    def test_the_result_is_cached_per_revision_and_an_edit_invalidates_it(self):
        first = VL.PIPELINE.validate(self.p)
        self.assertTrue(VL.PIPELINE.validate(self.p)["cached"])
        self.run_("model.set_property", id=self.cases["wall"][0], key="thickness", value=300)
        again = VL.PIPELINE.validate(self.p)
        self.assertFalse(again["cached"])
        self.assertEqual(again["revision"], first["revision"] + 1)


class Physical(Labelled):
    def test_layer_three_is_opt_in_and_never_invents_a_number(self):
        with self.assertRaises(BadRequest):
            VL.PIPELINE.validate(self.p, layers=[3])                                            # nothing asked for
        beam = self.cases["beam"][0]
        r = VL.PIPELINE.validate(self.p, layers=[3], physical=[{"id": beam, "load": 10, "support": "simply_supported", "load_type": "udl", "max_deflection_ratio": 250},
                                                                   {"id": "no-such-beam"}])
        self.assertEqual([a["id"] for a in r["analyses"]], [beam, "no-such-beam"])
        for a in r["analyses"]:
            if a["status"] == "unsupported":
                self.assertTrue(a["reason"])                                                    # the engine (or the element) is missing: said so, no result made up
                self.assertNotIn("outputs", a)
            else:
                self.assertIn("note", a)                                                        # an analysis always carries its not-a-certification note
        self.assertIn("professional structural certification", r["basis"])
        self.assertFalse([f for f in r["findings"] if f["layer"] != 3])

    def test_the_deflection_limit_is_the_callers_and_is_compared_in_metres(self):
        beam = self.cases["beam"][0]
        outputs = {"max_deflection": {"value": 30.0, "unit": "mm"}}
        over = VL.deflection_finding(self.p, beam, outputs, 6.0, 250)                          # 30 mm against 6000/250 = 24 mm
        self.assertEqual((over["layer"], over["rule"], over["severity"]), (3, "beam_deflection", "warning"))
        self.assertAlmostEqual(over["measurement"]["limit"], 0.024, places=12)
        self.assertIsNone(VL.deflection_finding(self.p, beam, outputs, 6.0, 200))               # against 30 mm: exactly at the limit passes
        self.assertIsNone(VL.deflection_finding(self.p, beam, {"max_deflection": {"value": 1.0, "unit": "furlong"}}, 6.0, 250))   # an unknown unit is not guessed


class Corrections(Labelled):
    def test_a_correction_is_a_proposal_never_applied_until_approved_and_undoable(self):
        thin = self.cases["wall 10 mm"][0]
        r = VL.PIPELINE.validate(self.p)
        finding = next(f for f in r["findings"] if f["ref"] == thin and f["rule"] == "min_thickness")
        self.assertEqual(finding["fix"]["steps"][0]["op"], "model.set_property")
        before = self.p.revision
        msg = VL.PIPELINE.propose_fix(self.p, r["revision"], [finding["id"]])
        self.assertEqual(self.p.revision, before)                                              # proposing changes nothing
        self.assertAlmostEqual(E.m(self.meta(thin), "thickness"), 0.01)
        done = ASSISTANT.approve(self.p, msg["proposal"]["id"], expected_revision=before)
        self.assertEqual(done["kind"], "success")
        self.assertAlmostEqual(E.m(self.meta(thin), "thickness"), 0.02)
        self.assertNotIn((1, "min_thickness"), self.reported(VL.PIPELINE.validate(self.p), thin))
        self.p.undo()
        self.assertAlmostEqual(E.m(self.meta(thin), "thickness"), 0.01)

    def test_a_finding_from_an_older_revision_is_refused_and_findings_without_a_correction_are_not_proposed(self):
        r = VL.PIPELINE.validate(self.p)
        fixable = [f["id"] for f in r["findings"] if f.get("fix")]
        self.run_("model.set_property", id=self.cases["wall"][0], key="height", value=3.3)
        with self.assertRaises(Conflict):
            VL.PIPELINE.propose_fix(self.p, r["revision"], fixable)
        current = VL.PIPELINE.validate(self.p)
        no_fix = [f["id"] for f in current["findings"] if not f.get("fix")]
        with self.assertRaises(BadRequest):
            VL.PIPELINE.propose_fix(self.p, current["revision"], no_fix)

    def test_the_project_methods_validate_their_input(self):
        out = self.p.layered_validation({"layers": [1], "limits": {"min_thickness_m": 0.015}})
        self.assertEqual([l["id"] for l in out["layers"]], [1])
        for bad in ({"layers": "1"}, {"layers": [True]}, {"physical": "x"}, {"limits": []}):
            with self.assertRaises(BadRequest):
                self.p.layered_validation(bad)
        with self.assertRaises(BadRequest):
            self.p.propose_validation_fix({"findings": []})


if __name__ == "__main__":
    unittest.main()
