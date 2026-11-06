"""A room's volume comes from what encloses it: the floor under it and the floor or roof over it, slopes included."""
from __future__ import annotations

import math

from applications.SPADRA import rooms as R
from applications.SPADRA.project import Project

from .test_bim import Case


class RoomVolume(Case):
    def room(self, level=None, outline=((0, 0), (5000, 0), (5000, 4000), (0, 4000))):
        return self.run_("model.add_room", level=level or self.l1, points=[list(p) for p in outline])["id"]

    def box(self, rid):
        return R.enclosure(self.p, rid)

    def test_with_nothing_above_the_height_parameter_is_used_and_said_so(self):
        rid = self.room()
        d = R.data(self.p, rid)
        self.assertEqual(d["volume_basis"], "height")
        self.assertAlmostEqual(d["volume"], 20.0 * R.DEFAULT_HEIGHT, places=6)

    def test_a_floor_above_closes_the_room_at_its_underside(self):
        rid = self.room()
        self.run_("model.add_floor", level=self.l2, points=[[-500, -500], [5500, -500], [5500, 4500], [-500, 4500]], thickness=300)
        elevation = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.l2)
        d = R.data(self.p, rid)
        self.assertEqual(d["volume_basis"], "enclosure")
        self.assertAlmostEqual(d["volume"], 20.0 * (elevation - 0.3), places=6)             # up to the underside of the 300 mm slab

    def test_the_floor_under_the_room_raises_its_bottom(self):
        rid = self.room()
        self.run_("model.add_floor", level=self.l1, points=[[-500, -500], [5500, -500], [5500, 4500], [-500, 4500]], thickness=200)
        self.run_("model.add_floor", level=self.l2, points=[[-500, -500], [5500, -500], [5500, 4500], [-500, 4500]], thickness=300)
        elevation = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.l2)
        d = R.data(self.p, rid)
        self.assertAlmostEqual(d["volume"], 20.0 * (elevation - 0.3 - 0.0), places=6)       # the floor's top is the level, so its thickness lies below it
        self.assertEqual(d["floor_surface"] is not None, True)

    def test_a_gable_roof_gives_the_exact_prism_volume(self):
        rid = self.room()
        roof_level = self.roof_level
        base = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == roof_level)
        self.run_("model.add_roof", level=roof_level, roof="gable", points=[[0, 0], [5000, 0], [5000, 4000], [0, 4000]], thickness=200, pitch=30)
        rise = 2.0 * math.tan(math.radians(30))                                              # width 4 m is the short side: the ridge runs along the 5 m
        d = R.data(self.p, rid)
        self.assertEqual(d["volume_basis"], "enclosure")
        expected = 5.0 * 4.0 * (base - 0.2) + 5.0 * (4.0 * rise / 2)                         # a box under the eaves' underside, plus the triangular gable prism
        self.assertAlmostEqual(d["volume"], expected, places=6)

    def test_a_pyramid_hip_roof_is_a_third_of_the_box_above_its_eaves(self):
        rid = self.room(outline=((0, 0), (4000, 0), (4000, 4000), (0, 4000)))
        base = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.roof_level)
        self.run_("model.add_roof", level=self.roof_level, roof="hip", points=[[0, 0], [4000, 0], [4000, 4000], [0, 4000]], thickness=200, pitch=45)
        d = R.data(self.p, rid)
        self.assertEqual(d["volume_basis"], "enclosure")
        self.assertAlmostEqual(d["volume"], 16.0 * (base - 0.2) + 16.0 * 2.0 / 3.0, places=6)    # pyramid: base × rise / 3, rise = 2 m at 45°

    def test_a_roof_that_covers_only_part_leaves_the_rest_to_the_height_parameter(self):
        rid = self.room()
        base = next(l["elevation"] for l in self.p.manifest["levels"] if l["id"] == self.roof_level)
        self.run_("model.add_roof", level=self.roof_level, roof="flat", points=[[0, 0], [2500, 0], [2500, 4000], [0, 4000]], thickness=100)
        d = R.data(self.p, rid)
        self.assertEqual(d["volume_basis"], "enclosure+height")
        self.assertAlmostEqual(d["covered"], 0.5, places=6)
        self.assertAlmostEqual(d["volume"], 10.0 * base + 10.0 * R.DEFAULT_HEIGHT, places=6)

    def test_the_plan_label_carries_the_volume(self):
        rid = self.room()
        plan = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == self.l1)
        label = next(w["label"] for w in self.p.plan_overlay(plan) if w["id"] == rid)
        self.assertAlmostEqual(label["volume_m3"], 20.0 * R.DEFAULT_HEIGHT, places=6)
        self.assertAlmostEqual(label["area_m2"], 20.0, places=6)

    def test_the_properties_panel_says_where_the_volume_comes_from(self):
        rid = self.room()
        rows = {r.get("key") or r["label"]: r for g in self.p.properties({"model": rid})["groups"] for r in g["rows"]}
        self.assertIn("area × height", rows["Volume"]["value"])
