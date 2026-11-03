"""Beam profiles: every family builds a closed solid whose volume is the section's area times the length, the section properties are the textbook ones
for shapes that have them, and the properties, analysis and schedule follow the profile."""
from __future__ import annotations

import math
import unittest

from applications.SPADRA import elements as E
from applications.SPADRA import profiles as PR
from applications.SPADRA.engineering_bridge import BRIDGE
from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest


class Tables(unittest.TestCase):
    def test_every_size_of_every_family_makes_a_valid_section(self):
        for fam, info in PR.FAMILIES.items():
            self.assertIn(PR.default_size(fam), info["sizes"], fam)
            for name, dims in info["sizes"].items():
                rs = PR.rolled(PR.rings(fam, dims), 0)
                props = PR.properties(rs)
                self.assertGreater(props["area_mm2"], 0, name)
                self.assertGreater(props["ix_mm4"], 0, name)
                verts, faces = PR.prism(rs, 1000.0)
                self.assertEqual(self.open_edges(faces), [], name)                                   # watertight: every edge is shared by two faces, opposed

    @staticmethod
    def open_edges(faces):
        seen = {}
        for a, b, c in faces:
            for e in ((a, b), (b, c), (c, a)):
                seen[e] = seen.get(e, 0) + 1
        return [e for e, n in seen.items() if seen.get((e[1], e[0]), 0) != n]

    def test_the_catalogue_names_the_families_the_ui_offers(self):
        keys = [f["key"] for f in PR.catalogue()["families"]]
        self.assertEqual(keys, ["HEA", "HEB", "HEM", "INP", "IPE", "UPE", "UNP", "L_equal", "L_unequal", "C", "CHS", "SHS", "RHS"])
        self.assertIn("not checked against a supplier", PR.NOTE)

    def test_known_designations_carry_the_tabulated_dimensions(self):
        self.assertEqual(PR.FAMILIES["IPE"]["sizes"]["IPE 200"], {"h": 200, "b": 100, "tw": 5.6, "tf": 8.5})
        self.assertEqual(PR.FAMILIES["HEA"]["sizes"]["HEA 200"], {"h": 190, "b": 200, "tw": 6.5, "tf": 10})
        self.assertEqual(PR.FAMILIES["HEB"]["sizes"]["HEB 300"], {"h": 300, "b": 300, "tw": 11, "tf": 19})
        self.assertEqual(PR.FAMILIES["UPE"]["sizes"]["UPE 200"], {"h": 200, "b": 80, "tw": 6, "tf": 11})
        self.assertEqual(PR.FAMILIES["CHS"]["sizes"]["CHS 88.9x4"], {"d": 88.9, "t": 4})
        self.assertEqual(PR.FAMILIES["L_unequal"]["sizes"]["L 100x75x8"], {"a": 100, "b": 75, "t": 8})

    def test_section_properties_are_exact_for_the_outline(self):
        # a solid rectangle b x h: A = bh, I = bh^3/12 ; a hollow square tube: outer less inner
        rect = [[(-50, -100), (50, -100), (50, 100), (-50, 100)]]
        p = PR.properties(rect)
        self.assertAlmostEqual(p["area_mm2"], 100 * 200)
        self.assertAlmostEqual(p["ix_mm4"], 100 * 200 ** 3 / 12, places=3)
        self.assertAlmostEqual(p["iz_mm4"], 200 * 100 ** 3 / 12, places=3)
        rs = PR.rings("SHS", {"a": 100, "t": 5})
        p = PR.properties(rs)
        self.assertAlmostEqual(p["ix_mm4"], (100 ** 4 - 90 ** 4) / 12, places=3)
        self.assertAlmostEqual(p["area_mm2"], 100 ** 2 - 90 ** 2)
        # an I section from its outline: 2 flanges + web
        d = {"h": 200, "b": 100, "tw": 5.6, "tf": 8.5}
        p = PR.properties(PR.rings("IPE", d))
        inertia = (d["b"] * d["h"] ** 3 - (d["b"] - d["tw"]) * (d["h"] - 2 * d["tf"]) ** 3) / 12
        self.assertAlmostEqual(p["ix_mm4"], inertia, places=3)
        self.assertAlmostEqual(p["area_mm2"], 2 * d["b"] * d["tf"] + d["tw"] * (d["h"] - 2 * d["tf"]))

    def test_an_angle_has_its_centroid_off_the_middle_and_rolling_turns_the_section(self):
        p = PR.properties(PR.rings("L_equal", {"a": 100, "b": 100, "t": 10}))
        self.assertAlmostEqual(p["area_mm2"], 1900)
        self.assertAlmostEqual(p["centroid_z_mm"], (1000 * -45 + 900 * 5) / 1900, places=6)           # below the middle: the foot is on the bottom
        a = PR.properties(PR.rolled(PR.rings("RHS", {"h": 200, "b": 100, "t": 6}), 0))
        b = PR.properties(PR.rolled(PR.rings("RHS", {"h": 200, "b": 100, "t": 6}), 90))
        self.assertAlmostEqual(a["ix_mm4"], b["iz_mm4"], places=3)                                   # turned on its side, the horizontal axis is the weak one
        self.assertAlmostEqual(a["iz_mm4"], b["ix_mm4"], places=3)
        w, d = PR.bounds(PR.rolled(PR.rings("RHS", {"h": 200, "b": 100, "t": 6}), 90))
        self.assertAlmostEqual(w, 200)
        self.assertAlmostEqual(d, 100)

    def test_bad_dimensions_are_refused(self):
        for fam, bad in (("IPE", {"h": 200, "b": 100, "tw": 5.6, "tf": 120}), ("IPE", {"h": 200, "b": 100, "tw": 0, "tf": 8}), ("L_equal", {"a": 50, "b": 50, "t": 60}),
                         ("C", {"h": 100, "b": 50, "c": 5, "t": 6}), ("CHS", {"d": 20, "t": 12}), ("SHS", {"a": 20, "t": 11}), ("RHS", {"h": 100, "b": 20, "t": 11}),
                         ("HEA", {"h": 9000, "b": 100, "tw": 5, "tf": 8})):
            with self.assertRaises(BadRequest):
                PR.validate(fam, bad)
        with self.assertRaises(BadRequest):
            PR.dims_of("IPE", "IPE 999")
        with self.assertRaises(BadRequest):
            PR.dims_of("IPE", "custom", {"h": 200})
        self.assertEqual(PR.dims_of("IPE", "custom", {"h": 210, "b": 100, "tw": 6, "tf": 9})["h"], 210)


class Beams(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Profiles")
        self.l1 = self.p.manifest["levels"][0]["id"]

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def beam(self, **extra):
        return self.run_("model.add_beam", start=[0, 0], end=[6000, 0], level=self.l1, top=3.0, **extra)["id"]

    def vol(self, oid):
        return self.p.model.volume(oid, unit="m")

    def meta(self, oid):
        return self.p.model.get(oid)["metadata"]

    def test_each_family_is_a_beam_of_the_volume_its_section_gives(self):
        for fam, info in PR.FAMILIES.items():
            size = PR.default_size(fam)
            b = self.beam(profile=fam, size=size)
            area = PR.properties(PR.rings(fam, info["sizes"][size]))["area_mm2"] / 1e6
            self.assertAlmostEqual(self.vol(b), area * 6.0, places=6, msg=size)
            (x0, y0, z0), (x1, y1, z1) = self.p.model.world_bounds(b)
            self.assertAlmostEqual(z1, 3.0, places=9, msg=size)                                      # the top of the section is the beam's top
            self.assertAlmostEqual(x0, 0.0, places=9)
            self.assertAlmostEqual(x1, 6.0, places=9)
            w, d = PR.bounds(PR.rings(fam, info["sizes"][size]))
            self.assertAlmostEqual(y1 - y0, w / 1000, places=9, msg=size)
            self.assertAlmostEqual(z1 - z0, d / 1000, places=9, msg=size)
            self.assertAlmostEqual(self.meta(b)[f"{NS}:depth"], d / 1000, places=9)
            self.assertEqual(self.meta(b)[f"{NS}:material"], "Steel")

    def test_the_original_rectangular_beam_is_unchanged(self):
        b = self.beam(width=200, depth=400)
        self.assertEqual(self.meta(b)[f"{NS}:profile"], "rect")
        self.assertAlmostEqual(self.vol(b), 0.2 * 0.4 * 6.0, places=9)

    def test_editing_the_profile_the_size_the_dimensions_and_the_roll(self):
        b = self.beam(width=200, depth=400)
        self.run_("model.set_property", id=b, key="profile", value="IPE")
        self.assertEqual((self.meta(b)[f"{NS}:size"], self.meta(b)[f"{NS}:dims"]["h"]), ("IPE 200", 200))
        self.run_("model.set_property", id=b, key="size", value="IPE 300")
        self.assertAlmostEqual(self.p.model.world_bounds(b)[1][2] - self.p.model.world_bounds(b)[0][2], 0.3, places=9)
        self.run_("model.set_property", id=b, key="dim:tf", value=12)                                # one dimension changed: the size is now custom
        self.assertEqual(self.meta(b)[f"{NS}:size"], "custom")
        area = PR.properties(PR.rings("IPE", {"h": 300, "b": 150, "tw": 7.1, "tf": 12}))["area_mm2"] / 1e6
        self.assertAlmostEqual(self.vol(b), area * 6, places=6)
        self.run_("model.set_property", id=b, key="roll", value=90)
        (x0, y0, z0), (x1, y1, z1) = self.p.model.world_bounds(b)
        for got, want in zip((y1 - y0, z1 - z0, z1), (0.3, 0.15, 3.0)):                               # on its side, top still at the beam's top
            self.assertAlmostEqual(got, want, places=9)
        self.run_("model.set_property", id=b, key="profile", value="rect")
        self.assertAlmostEqual(self.vol(b), float(self.meta(b)[f"{NS}:width"]) * float(self.meta(b)[f"{NS}:depth"]) * 6, places=9)
        self.p.undo()
        self.assertEqual(self.meta(b)[f"{NS}:profile"], "IPE")

    def test_what_a_profile_beam_cannot_be_is_refused_and_leaves_it_as_it_was(self):
        b = self.beam(profile="IPE", size="IPE 200")
        before = self.vol(b)
        for key, value in (("width", 300), ("depth", 300), ("size", "IPE 12"), ("dim:tf", 500), ("dim:zz", 1), ("roll", 400), ("material", "Concrete"), ("profile", "ZZ")):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=b, key=key, value=value)
        self.assertAlmostEqual(self.vol(b), before, places=12)
        with self.assertRaises(BadRequest):
            self.beam(profile="IPE", material="Concrete")
        with self.assertRaises(BadRequest):
            self.beam(profile="IPE", size="IPE 12")
        rect = self.beam(width=200, depth=400)
        for key, value in (("size", "x"), ("roll", 10), ("dim:h", 100)):
            with self.assertRaises(BadRequest):
                self.run_("model.set_property", id=rect, key=key, value=value)
        self.run_("model.set_property", id=rect, key="material", value="Concrete")
        with self.assertRaises(BadRequest):
            self.run_("model.set_property", id=rect, key="profile", value="HEA")                     # a concrete beam is not changed to steel on its own

    def test_a_custom_size_and_a_beam_that_moves_stay_in_place(self):
        b = self.beam(profile="C", size="custom", dims={"h": 180, "b": 70, "c": 20, "t": 2.5})
        self.assertEqual(self.meta(b)[f"{NS}:size"], "custom")
        self.run_("model.move", id=b, dx=1.0, dy=2.0)
        (x0, y0, z0), (x1, y1, z1) = self.p.model.world_bounds(b)
        for got, want in zip((x0, x1, z1), (1.0, 7.0, 3.0)):
            self.assertAlmostEqual(got, want, places=9)
        self.assertAlmostEqual((y0 + y1) / 2, 2.0, places=9)
        self.run_("model.rotate", ids=[b], centre=[0, 0], angle=90)
        (x0, y0, _), (x1, y1, _) = self.p.model.world_bounds(b)
        self.assertAlmostEqual(x1 - x0, 0.07, places=9)
        self.assertAlmostEqual(y1 - y0, 6.0, places=9)

    def test_properties_schedule_and_quantities_follow_the_profile(self):
        from applications.SPADRA import documentation as D
        b = self.beam(profile="HEA", size="HEA 240")
        top = {r["key"]: r for r in E.properties(self.p, b)["top"] if "key" in r}
        self.assertEqual((top["profile"]["value"], top["size"]["value"]), ("HEA", "HEA 240"))
        self.assertTrue({"dim:h", "dim:b", "dim:tw", "dim:tf", "roll", "material"} <= set(top))
        self.assertNotIn("width", top)
        self.assertEqual([c["value"] for c in top["profile"]["choices"]][:3], ["rect", "HEA", "HEB"])
        rows = [r["label"] for g in E.properties(self.p, b)["groups"] for r in g["rows"]]
        self.assertTrue({"Section area", "Second moment (horizontal axis)", "Overall size"} <= set(rows))
        row = D.schedule_data(self.p, "structural")["rows"][0]
        self.assertEqual((row["profile"], row["type"]), ("HEA 240", "Beam"))
        q = {r["id"]: r for r in self.p.quantities()["rows"]}[b]
        self.assertAlmostEqual(q["volume_m3"], PR.properties(PR.rings("HEA", PR.FAMILIES["HEA"]["sizes"]["HEA 240"]))["area_mm2"] / 1e6 * 6, places=6)
        self.assertEqual(E.check(self.p), [])

    def test_survives_save_and_reopen(self):
        import tempfile
        from pathlib import Path
        b = self.beam(profile="RHS", size="RHS 100x60x5", roll=90)
        with tempfile.TemporaryDirectory() as tmp:
            self.p.save(Path(tmp) / "p.spadra")
            q = Project.open(Path(tmp) / "p.spadra")
            self.assertEqual(q.model.get(b)["metadata"][f"{NS}:dims"], {"h": 100, "b": 60, "t": 5})
            self.assertAlmostEqual(q.model.volume(b, unit="m"), self.vol(b), places=12)


@unittest.skipUnless(BRIDGE.available, f"the SLAI STEM Agent is not importable here ({BRIDGE.reason})")
class Analysis(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Analysis")
        self.l1 = self.p.manifest["levels"][0]["id"]

    def run_(self, op, **params):
        return self.p.execute(op, params)["result"]

    def analyse(self, **beam):
        b = self.run_("model.add_beam", start=[0, 0], end=[5000, 0], level=self.l1, top=3.0, **beam)["id"]
        return self.p.analyze_beam(b, {"load": 10, "load_type": "udl", "support": "simply_supported"})

    def test_the_second_moment_is_the_profiles_and_the_deflection_follows_it(self):
        ipe = self.analyse(profile="IPE", size="IPE 200")
        expected = PR.properties(PR.rings("IPE", PR.FAMILIES["IPE"]["sizes"]["IPE 200"]))["ix_mm4"] / 1e12
        self.assertAlmostEqual(ipe["inputs"]["second_moment_m4"], expected, places=15)
        self.assertEqual(ipe["inputs"]["profile"], "IPE 200")
        self.assertIn("outline", ipe["inputs"]["section_source"])
        rect = self.analyse(width=100, depth=200)
        ratio = rect["inputs"]["second_moment_m4"] / ipe["inputs"]["second_moment_m4"]
        d = lambda r: r["outputs"]["max_deflection_m"]["value"]                                        # noqa: E731
        self.assertAlmostEqual(d(ipe) / d(rect), ratio, places=9)                                       # w L^4 / (384/5 E I): inversely proportional to I
        self.assertEqual(rect["inputs"]["profile"], "Rectangular")

    def test_round_and_rectangular_tubes_use_the_engines_own_section_properties(self):
        chs = self.analyse(profile="CHS", size="CHS 88.9x4")
        d, t = 88.9e-3, 4e-3
        self.assertAlmostEqual(chs["inputs"]["second_moment_m4"], math.pi * (d ** 4 - (d - 2 * t) ** 4) / 64, places=15)
        self.assertIn("hollow circle", chs["inputs"]["section_source"])
        rhs = self.analyse(profile="RHS", size="RHS 100x60x5")
        self.assertAlmostEqual(rhs["inputs"]["second_moment_m4"], (0.06 * 0.1 ** 3 - 0.05 * 0.09 ** 3) / 12, places=15)
        self.assertIn("outer less inner", rhs["inputs"]["section_source"])
        rolled = self.analyse(profile="RHS", size="RHS 100x60x5", roll=90)
        self.assertAlmostEqual(rolled["inputs"]["second_moment_m4"], (0.1 * 0.06 ** 3 - 0.09 * 0.05 ** 3) / 12, places=15)


if __name__ == "__main__":
    unittest.main()
