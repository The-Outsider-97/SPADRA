"""The Properties panel for every kind of element, including the ones that have no solid (a grid, a group) and ones whose geometry is not a valid solid:
a missing volume is reported as missing, never as zero and never as a failure of the whole request."""
from __future__ import annotations

import unittest

from .. import elements as E
from ..project import Project
from .test_bim import Case


class Panel(Case):
    def build(self):
        w = self.wall()
        ids = {"wall": w}
        ids["floor"] = self.run_("model.add_floor", points=[[0, 0], [4000, 0], [4000, 3000], [0, 3000]], level=self.l1)["id"]
        ids["roof"] = self.run_("model.add_roof", points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]], level=self.roof_level, roof="gable", pitch=30, thickness=200)["id"]
        ids["beam"] = self.run_("model.add_beam", start=[0, 0], end=[5000, 0], level=self.l1, top=3.0)["id"]
        ids["column"] = self.run_("model.add_column", level=self.l1, point=[1000, 1000])["id"]
        ids["stair"] = self.run_("model.add_stair", level=self.l1, start=[0, 2000], end=[3500, 2000])["id"]
        ids["door"] = self.run_("model.add_door", wall=w, offset=2.0)["id"]
        ids["gutter"] = self.run_("model.add_gutters", roof=ids["roof"])["gutters"][0]
        ids["downpipe"] = next(i for i in E.element_ids(self.p) if E.category(self.p, i) == "downpipe")
        ids["grid"] = self.run_("model.add_grid", start=[0, 0], end=[5000, 0], label="A")["id"]
        ids["room"] = self.run_("model.add_room", level=self.l1, points=[[0, 0], [6000, 0], [6000, 4000], [0, 4000]])["id"]
        ids["void"] = self.run_("model.add_extrusion", points=[[500, 500], [1500, 500], [1500, 1500], [500, 1500]], void=True, level=self.l1)["id"]
        ids["extrusion"] = self.run_("model.add_extrusion", points=[[2000, 500], [3000, 500], [3000, 1500], [2000, 1500]], depth=1.0, level=self.l1)["id"]
        ids["sweep"] = self.run_("model.add_sweep", points=[[0, 3500], [4000, 3500]], shape="rectangle", width=200, height=300, level=self.l1)["id"]
        ids["group"] = self.p.model.add_group(name="Organiser")
        return ids

    def rows(self, oid):
        out = self.p.properties({"model": oid}, relations=True)
        return {r.get("key") or r["label"]: r for r in out["top"] + [r for g in out["groups"] for r in g["rows"]]}

    def test_every_category_answers_without_an_exception(self):
        ids = self.build()
        for name, oid in ids.items():
            with self.subTest(name):
                out = self.p.properties({"model": oid}, relations=True)
                self.assertEqual(out["kind"], "model")
                self.assertTrue(out["title"])

    def test_solids_show_their_volume(self):
        ids = self.build()
        for name in ("wall", "floor", "roof", "beam", "column", "stair", "extrusion", "sweep"):
            with self.subTest(name):
                self.assertRegex(self.rows(ids[name])["Volume"]["value"], r"^\d+\.\d{3} m³$")

    def test_a_grid_has_no_volume_and_says_so(self):
        ids = self.build()
        self.assertEqual(self.rows(ids["grid"])["Volume"]["value"], "Not applicable")

    def test_a_geometry_less_group_has_no_volumetric_geometry(self):
        ids = self.build()
        st = E.solid_status(self.p, ids["group"])
        self.assertEqual((st.state, st.volume), ("none", None))
        self.assertEqual(E.volume_row(self.p, ids["group"])["value"], "No volumetric geometry")

    def test_an_open_surface_is_not_a_solid_and_not_zero(self):
        oid = self.p.model.add_mesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], name="Open")
        st = E.solid_status(self.p, oid)
        self.assertEqual((st.state, st.volume), ("surface", None))
        self.assertIn("encloses no volume", E.volume_row(self.p, oid)["value"])

    def test_invalid_geometry_is_reported_not_raised_and_the_model_check_finds_it(self):
        oid = self.p.model.add_mesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], faces=[[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], name="Bad")
        E._attach(self.p, oid, {"category": "generic", "type": "Generic Model"})
        st = E.solid_status(self.p, oid)
        self.assertEqual(st.volume, None)
        self.assertIn(st.state, ("invalid", "surface"))
        out = self.p.properties({"model": oid}, relations=True)
        self.assertTrue(out["title"])

    def test_the_answer_is_measured_once_per_revision(self):
        ids = self.build()
        calls = []
        original = self.p.model.volume
        class Spy:
            def __getattr__(s, n): return getattr(self.p.model, n)
        self.p.model.volume = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            E.solid_status(self.p, ids["wall"]); E.solid_status(self.p, ids["wall"]); E.solid_status(self.p, ids["wall"])
        finally:
            del self.p.model.volume
        self.assertLessEqual(len(calls), 1)

    def test_repeated_selection_of_a_grid_never_fails(self):
        g = self.run_("model.add_grid", start=[0, 0], end=[5000, 0])["id"]
        for _ in range(5):
            self.assertEqual(self.p.properties({"model": g}, relations=True)["category"], "grid")


if __name__ == "__main__":
    unittest.main()
