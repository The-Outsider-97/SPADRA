"""Control over what a void cuts: the Cut Intersecting Geometry switch and the four policies, a classification of everything a void meets that is made from
the geometry (not from bounding boxes), cuts that stay non-destructive, and a plain report of what is not cut and why."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import cutting as CU
from applications.SPADRA import elements as E
from applications.SPADRA import forms as FX
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest

SQUARE = [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]
RECT = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Cutting")
        self.l1, self.l2, self.roof_level = (l["id"] for l in self.p.manifest["levels"])

    def run_(self, op, **p):
        return self.p.execute(op, p)["result"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def floor(self, x=0, w=6000, d=5000):
        return self.run_("model.add_floor", level=self.l1, points=[[x, 0], [x + w, 0], [x + w, d], [x, d]], thickness=200)["id"]

    def void(self, points=SQUARE, **extra):
        return self.run_("model.add_extrusion", level=self.l1, points=points, void=True, **extra)["id"]

    def set(self, oid, key, value):
        return self.run_("model.set_property", id=oid, key=key, value=value)

    def classes(self, vid):
        return {r["name"]: r for r in CU.classify(self.p, vid)}

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]


class Switch(Case):
    def test_turning_cutting_off_gives_the_element_back_whole_and_on_cuts_it_again(self):
        f = self.floor()
        v = self.void()
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.set(v, "cut_enabled", "Disabled")
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)
        self.assertEqual(self.meta(f).get(f"{NS}:cut"), [])                       # nothing about the void is kept in the floor
        item = next(i for i in E.plan_items(self.p, self.l1, 1000.0) if i["id"] == f)
        self.assertNotIn("holes", item)
        self.set(v, "cut_enabled", "Enabled")
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.p.undo()
        self.assertAlmostEqual(self.vol(f), 6.0, places=9)                        # one undo takes the last switch back
        self.p.undo()
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)

    def test_a_void_made_before_the_settings_existed_cuts_everything_it_can(self):
        f = self.floor()
        v = self.void()
        self.assertEqual(CU.settings(self.meta(v)), {"enabled": True, "policy": "automatic", "targets": [], "host": None})
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)

    def test_the_settings_can_be_given_when_the_void_is_made(self):
        f = self.floor()
        v = self.void(cut_policy="targets", targets=[f])
        self.assertEqual(CU.settings(self.meta(v)), {"enabled": True, "policy": "targets", "targets": [f], "host": None})
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        off = self.void([[3000, 1000], [4000, 1000], [4000, 2000], [3000, 2000]], cut_enabled=False)
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)                         # the second void is made off
        self.assertFalse(CU.settings(self.meta(off))["enabled"])
        with self.assertRaises(BadRequest):
            self.void(cut_policy="everything")
        with self.assertRaises(Exception):
            self.void(targets=["nope"])


class Policies(Case):
    def setUp(self) -> None:
        super().setUp()
        self.a, self.b = self.floor(0, 3000), self.floor(3000, 3000)                # two floors side by side
        self.cross = [[2500, 1000], [3500, 1000], [3500, 2000], [2500, 2000]]          # across their shared edge: only a void wholly inside one is cut

    def test_automatic_cuts_every_floor_it_lies_inside_and_selected_targets_only_the_chosen_ones(self):
        v = self.void([[500, 1000], [1000, 1000], [1000, 1500], [500, 1500]])          # wholly inside floor a
        w = self.void([[3500, 1000], [4000, 1000], [4000, 1500], [3500, 1500]])        # wholly inside floor b
        da, db = 3 * 5 * 0.2 - 0.5 * 0.5 * 0.2, 3 * 5 * 0.2 - 0.5 * 0.5 * 0.2
        self.assertAlmostEqual(self.vol(self.a), da, places=9)
        self.assertAlmostEqual(self.vol(self.b), db, places=9)
        self.set(v, "cut_policy", "targets")                                          # none chosen yet
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)
        self.set(v, "target_add", self.b)                                             # the wrong floor: v is not inside b
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)
        self.assertEqual([(r["name"], r["state"]) for r in CU.classify(self.p, v)], [("Floor", "excluded")])           # a is reached, but it is not a target
        self.set(v, "target_add", self.a)
        self.assertAlmostEqual(self.vol(self.a), da, places=9)
        self.assertEqual(CU.settings(self.meta(v))["targets"], [self.b, self.a])
        self.set(v, "target_remove", self.a)
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)
        self.assertAlmostEqual(self.vol(self.b), db, places=9)                         # w was never touched
        with self.assertRaises(BadRequest):
            self.set(v, "target_remove", self.a)                                      # it is not there any more

    def test_hosted_cuts_only_its_host_and_disabled_cuts_nothing(self):
        v = self.void([[500, 1000], [1000, 1000], [1000, 1500], [500, 1500]])
        self.set(v, "cut_policy", "hosted")
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)                       # no host chosen
        self.set(v, "host", self.a)
        self.assertAlmostEqual(self.vol(self.a), 2.95, places=9)
        self.set(v, "host", self.b)
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)
        self.set(v, "host", "")
        self.assertIsNone(CU.settings(self.meta(v))["host"])
        self.set(v, "cut_policy", "disabled")
        self.assertAlmostEqual(self.vol(self.a), 3.0, places=9)
        self.set(v, "cut_policy", "automatic")
        self.assertAlmostEqual(self.vol(self.a), 2.95, places=9)

    def test_a_host_is_not_a_hosting_relation_so_deleting_the_host_does_not_delete_the_void(self):
        v = self.void()
        self.set(v, "cut_policy", "hosted")
        self.set(v, "host", self.a)
        self.assertEqual(E.hosted(self.p, self.a), [])
        self.run_("model.delete", id=self.a)
        self.assertTrue(E.alive(self.p, v))
        self.assertTrue(any("no longer exists" in m for m in (i["message"] for i in E.check(self.p))))

    def test_bad_settings_are_refused_and_change_nothing(self):
        v = self.void()
        before = self.p.revision
        for key, bad in (("cut_policy", "some"), ("cut_enabled", "maybe"), ("target_add", "nope"), ("target_add", v), ("host", v), ("host", "nope"), ("target_remove", self.a)):
            with self.assertRaises(Exception, msg=key):
                self.set(v, key, bad)
        self.assertEqual(self.p.revision, before)

    def test_settings_survive_save_and_reopen(self):
        v = self.void([[500, 1000], [1000, 1000], [1000, 1500], [500, 1500]], cut_policy="targets", targets=[self.a])
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "c.spadra")
            q = Project.open(Path(tmp) / "c.spadra")
            self.assertEqual(CU.settings(q.model.get(v)["metadata"]), {"enabled": True, "policy": "targets", "targets": [self.a], "host": None})
            self.assertAlmostEqual(q.model.volume(self.a, unit="m"), 2.95, places=9)
            self.assertAlmostEqual(q.model.volume(self.b, unit="m"), 3.0, places=9)


class Classification(Case):
    def test_every_element_a_void_meets_is_cut_unsupported_or_excluded_with_a_reason(self):
        f = self.floor()
        w = self.run_("model.add_wall", level=self.l1, start=[0, 1500], end=[6000, 1500], thickness=200, height=3)["id"]
        roof = self.run_("model.add_roof", level=self.roof_level, points=RECT, roof="gable", pitch=30, thickness=200)["id"]
        beam = self.run_("model.add_beam", level=self.l1, start=[500, 1500], end=[5500, 1500], top=2.5)["id"]
        v = self.void([[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], base=-0.5, depth=4.0)       # through the floor, the wall and across the beam
        rows = {r["id"]: r for r in CU.classify(self.p, v)}
        self.assertEqual(rows[f]["state"], "cut")
        self.assertEqual(rows[f]["kind"], "hole")
        self.assertEqual((rows[w]["state"], rows[w]["kind"]), ("cut", "opening"))        # square across the wall and through it: an opening
        niche_wall = self.run_("model.add_wall", level=self.l1, start=[0, 3500], end=[6000, 3500], thickness=200, height=3)["id"]
        niche = self.void([[1000, 3450], [2000, 3450], [2000, 3550], [1000, 3550]], base=0.0, depth=2.0)                # inside the wall: stops short of its faces
        row = {r["id"]: r for r in CU.classify(self.p, niche)}[niche_wall]
        self.assertEqual(row["state"], "unsupported")
        self.assertIn("stops short", row["reason"])
        self.assertEqual(rows[beam]["state"], "unsupported")
        self.assertIn("not supported", rows[beam]["reason"])
        self.assertNotIn(roof, rows)                                                    # 6 m below the roof: not reached
        self.assertAlmostEqual(self.vol(f), 5.8, places=9)
        self.assertGreater(self.vol(beam), 0)                                          # the beam is left whole: nothing is faked

    def test_a_void_across_a_floors_edge_is_classified_as_a_notch_and_one_that_would_split_it_as_unsupported(self):
        f = self.floor()
        notch = self.void([[-500, 1000], [500, 1000], [500, 2000], [-500, 2000]], base=-0.5, depth=4.0)
        row = {r["id"]: r for r in CU.classify(self.p, notch)}[f]
        self.assertEqual((row["state"], row["kind"]), ("cut", "notch"))
        splitter = self.void([[2000, -100], [3000, -100], [3000, 5100], [2000, 5100]], base=-0.5, depth=4.0)
        row = {r["id"]: r for r in CU.classify(self.p, splitter)}[f]
        self.assertEqual(row["state"], "unsupported")
        self.assertIn("separate pieces", row["reason"])

    def test_a_wall_opening_is_classified_as_an_opening_and_the_policy_can_leave_it_alone(self):
        w = self.run_("model.add_wall", level=self.l1, start=[0, 0], end=[6000, 0], thickness=200, height=3)["id"]
        v = self.void([[1000, -300], [2000, -300], [2000, 300], [1000, 300]], base=0.0, depth=2.1)
        self.assertEqual((self.classes(v)["Basic Wall"]["state"], self.classes(v)["Basic Wall"]["kind"]), ("cut", "opening"))
        self.set(v, "cut_policy", "targets")
        row = self.classes(v)["Basic Wall"]
        self.assertEqual((row["state"], row["would"]), ("excluded", "opening"))
        self.assertIn("not one of the void's selected targets", row["reason"])
        self.assertAlmostEqual(self.vol(w), 6 * 3 * 0.2, places=9)                    # whole again

    def test_a_void_in_the_air_above_a_roof_is_not_reached_though_it_is_inside_the_roofs_bounding_box(self):
        roof = self.run_("model.add_roof", level=self.roof_level, points=RECT, roof="gable", pitch=30, thickness=200)["id"]
        lo, hi = self.p.model.world_bounds(roof)
        corner = [[100, 100], [400, 100], [400, 400], [100, 400]]
        z = E.level_elevation(self.p, self.roof_level)
        high = self.run_("model.add_extrusion", level=self.roof_level, points=corner, void=True, base=1.4, depth=0.3)["id"]     # near the eave, 1.4 m up
        self.assertTrue(lo[2] < z + 1.4 < hi[2])                                      # inside the roof's bounding box in all three directions
        self.assertTrue(lo[0] < 0.2 and hi[0] > 0.4 and lo[1] < 0.2 and hi[1] > 0.4)
        self.assertEqual(CU.classify(self.p, high), [])                               # the roof's actual surface is far below it
        ridge = self.run_("model.add_extrusion", level=self.roof_level, points=[[4000, 2800], [4400, 2800], [4400, 3200], [4000, 3200]], void=True, base=1.2, depth=1.0)["id"]
        rows = CU.classify(self.p, ridge)
        self.assertEqual([(r["id"], r["state"]) for r in rows], [(roof, "unsupported")])
        self.assertIn("roof is not supported", rows[0]["reason"])

    def test_a_void_wholly_inside_a_solid_is_found_by_the_solid_not_by_its_faces(self):
        ext = self.run_("model.add_extrusion", level=self.l1, points=[[0, 0], [4000, 0], [4000, 4000], [0, 4000]], depth=3.0, base=0.0)["id"]
        pocket = self.void([[1500, 1500], [2500, 1500], [2500, 2500], [1500, 2500]], base=1.0, depth=1.0)
        rows = self.classes(pocket)["Extrusion"]
        self.assertEqual(rows["state"], "unsupported")                                 # an extrusion is cut only through its whole depth: this pocket is inside it
        self.assertIn("pocket", rows["reason"])
        self.assertAlmostEqual(self.vol(ext), 48.0, places=9)

    def test_mesh_prism_test_is_exact(self):
        cube = ([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [0, 0, 2], [2, 0, 2], [2, 2, 2], [0, 2, 2]],
                [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        sq = lambda x0, y0, x1, y1: [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]            # noqa: E731
        self.assertTrue(CU.mesh_meets_prism(*cube, sq(1, 1, 3, 3), 0.5, 1.0))           # crosses a side
        self.assertTrue(CU.mesh_meets_prism(*cube, sq(0.5, 0.5, 1.5, 1.5), 0.5, 1.0))   # wholly inside the cube, touching no face
        self.assertFalse(CU.mesh_meets_prism(*cube, sq(3, 3, 4, 4), 0.0, 2.0))          # beside it
        self.assertFalse(CU.mesh_meets_prism(*cube, sq(0.5, 0.5, 1.5, 1.5), 2.5, 3.0))  # above it
        self.assertFalse(CU.mesh_meets_prism(*cube, sq(2, 0, 3, 2), 0.0, 2.0))          # touching a face only: no shared volume
        self.assertTrue(CU.mesh_meets_prism(*cube, [(0.5, 0.5), (3, 0.5), (3, 1), (1, 1), (1, 3), (0.5, 3)], 0.5, 1.5))         # a concave outline


class Panel(Case):
    def test_the_panel_has_geometry_cutting_and_diagnostics_and_reports_what_is_not_cut(self):
        f = self.floor()
        beam = self.run_("model.add_beam", level=self.l1, start=[500, 1500], end=[5500, 1500], top=2.5)["id"]
        v = self.void()
        panel = E.properties(self.p, v)
        self.assertEqual([g["title"] for g in panel["groups"]][:4], ["Geometry", "Constraints", "Cutting", "Diagnostics"])
        cutting = {r["key"]: r for r in next(g for g in panel["groups"] if g["title"] == "Cutting")["rows"] if "key" in r}
        self.assertEqual((cutting["cut_enabled"]["label"], cutting["cut_enabled"]["value"]), ("Cut Intersecting Geometry", "Enabled"))
        self.assertEqual([c["value"] for c in cutting["cut_policy"]["choices"]], ["automatic", "targets", "hosted", "disabled"])
        diag = {r["label"]: r["value"] for r in next(g for g in panel["groups"] if g["title"] == "Diagnostics")["rows"]}
        self.assertEqual(diag["Effect"], "1 cut, 1 not supported, 0 left alone by the policy")
        self.assertTrue(diag["Floor (floor)"].startswith("Cut:"))
        self.assertTrue(next(v for k, v in diag.items() if k.startswith("Beam")).startswith("Not cut:"))
        self.set(v, "cut_policy", "targets")
        rows = next(g for g in E.properties(self.p, v)["groups"] if g["title"] == "Cutting")["rows"]
        self.assertIn("target_add", {r.get("key") for r in rows})
        self.assertIn("none chosen", next(r["value"] for r in rows if r["label"] == "Selected targets"))

    def test_the_model_check_names_what_is_reached_but_not_cut_and_a_void_that_cuts_nothing(self):
        self.floor()
        beam = self.run_("model.add_beam", level=self.l1, start=[500, 1500], end=[5500, 1500], top=2.5)["id"]
        v = self.void()
        msgs = [i["message"] for i in E.check(self.p)]
        self.assertTrue(any("Beam" in m and "not supported" in m for m in msgs), msgs)
        self.set(v, "cut_policy", "targets")
        self.assertTrue(any("none is chosen" in i["message"] for i in E.check(self.p)))
        self.set(v, "cut_policy", "disabled")
        self.assertEqual([i for i in E.check(self.p) if "none is chosen" in i["message"] or "does not touch" in i["message"]], [])      # the user chose it: no warning

    def test_the_report_is_available_without_the_panel(self):
        f = self.floor()
        v = self.void()
        rep = self.p.cutting_report(v)
        self.assertEqual((rep["void"], rep["settings"]["policy"], [r["state"] for r in rep["elements"]]), (v, "automatic", ["cut"]))
        with self.assertRaises(Exception):
            self.p.cutting_report("nope")


if __name__ == "__main__":
    unittest.main()


class LayeredFloorWithVoid(Case):
    """A floor built of layers keeps one cut list per layer; the Properties panel, the void classification and the model check must read them all
    (they once indexed a layer's cut list as if it were a wall's opening span and failed with IndexError)."""

    def test_properties_check_and_classification_of_a_layered_floor_cut_by_a_void(self):
        f = self.floor()
        self.run_("model.add_layer", id=f, material="Screed", thickness=60)
        v = self.void()
        rows = self.p.properties({"model": f}, relations=True)
        self.assertTrue(rows["groups"])
        self.p.properties({"model": v}, relations=True)
        self.assertEqual(self.classes(v)["Generic Floor"]["state"] if "Generic Floor" in self.classes(v) else "cut", "cut")
        self.assertIn("issues", self.p.check())
        self.assertEqual(FX.cut_ids(FX._cuts(self.p, f, FX.voids(self.p))[2]), {v})
