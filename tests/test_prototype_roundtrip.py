"""P1-03 (report Table 17): three procedural family prototypes (a façade, a structural frame and a designed stair) regenerate and round-trip through save,
reopen and SGEO export/import *without leaking parallel state*: every generated element is the model's own object, every recipe refers only to objects that
exist, and a deleted prototype leaves nothing behind."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA import procedural as PG
from applications.SPADRA.project import NS, Project


def element_rows(p: Project) -> list[tuple]:
    """Every model object as geometry and placement only (no ids): what a person would see, independent of how it was named."""
    rows = []
    for oid in p.model.query_ids(limit=10000):
        o = p.model.get(oid)
        rows.append((o.get("name"), json.dumps(o.get("geometry"), sort_keys=True, default=str), json.dumps([round(v, 9) for v in o["transform"]]),
                     o["metadata"].get(f"{NS}:category")))
    return sorted(rows)


def with_ids(p: Project) -> str:
    return hashlib.sha256(json.dumps([(oid, p.model.get(oid)["transform"], p.model.get(oid).get("geometry")) for oid in sorted(p.model.query_ids(limit=10000))],
                                     sort_keys=True, default=str).encode()).hexdigest()


class Prototypes(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project.create("Prototypes")
        self.l0 = self.p.manifest["levels"][0]["id"]
        self.x("model.add_wall", level=self.l0, start=[0, 0], end=[8000, 0], thickness=200, height=3.0)
        self.wall = next(i for i in self.p.model.query_ids(limit=1000) if self.p.model.get(i)["metadata"].get(f"{NS}:category") == "wall")
        self.x("model.add_door", wall=self.wall, offset=2.0, width=900)
        self.facade = self.x("procedural.facade", wall=self.wall, panel_width=1500, panel_height=1000)["id"]
        self.frame = self.x("procedural.frame", level=self.l0, origin=[0, 6000], bays_x=[5000, 5000], bays_y=[4000])["id"]
        self.stair = self.x("model.design_stair", level=self.l0, start=[10000, 0], end=[14500, 0], width=1000, layout="L", turn="left")

    def x(self, op, **kw):
        return self.p.execute(op, kw)["result"]

    # ------------------------------------------------------------------------------------------------ no parallel state
    def test_every_recipe_member_is_a_real_model_object_and_every_generated_object_is_in_a_recipe(self) -> None:
        ids = set(self.p.model.query_ids(limit=10000))
        for g in self.p.manifest["procedural"]:
            self.assertTrue(g["members"], g["id"])
            self.assertLessEqual(set(g["members"].values()), ids, f"group {g['id']} names objects the model does not have")
        recorded = {oid for g in self.p.manifest["procedural"] for oid in g["members"].values()}
        tagged = {i for i in ids if self.p.model.get(i)["metadata"].get(f"{NS}:procedural")}
        self.assertEqual(tagged, recorded, "a generated object is tagged without a recipe, or a recipe member is not tagged")

    # ------------------------------------------------------------------------------------------------ regeneration
    def test_regenerating_unchanged_prototypes_reproduces_the_same_model_and_the_same_ids(self) -> None:
        before = with_ids(self.p)
        for gid in (self.facade, self.frame):
            self.x("procedural.regenerate", id=gid)
        self.assertEqual(with_ids(self.p), before)

    def test_generation_is_deterministic_across_independent_projects(self) -> None:
        other = Project.create("Prototypes")
        level = other.manifest["levels"][0]["id"]
        other.execute("model.add_wall", {"level": level, "start": [0, 0], "end": [8000, 0], "thickness": 200, "height": 3.0})
        wall = next(i for i in other.model.query_ids(limit=1000) if other.model.get(i)["metadata"].get(f"{NS}:category") == "wall")
        other.execute("model.add_door", {"wall": wall, "offset": 2.0, "width": 900})
        other.execute("procedural.facade", {"wall": wall, "panel_width": 1500, "panel_height": 1000})
        other.execute("procedural.frame", {"level": level, "origin": [0, 6000], "bays_x": [5000, 5000], "bays_y": [4000]})
        other.execute("model.design_stair", {"level": level, "start": [10000, 0], "end": [14500, 0], "width": 1000, "layout": "L", "turn": "left"})
        self.assertEqual(element_rows(other), element_rows(self.p))

    # ------------------------------------------------------------------------------------------------ round trips
    def test_save_and_reopen_give_back_the_identical_model_and_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.spadra"
            self.p.save(path)
            q = Project.open(path)
        self.assertEqual(with_ids(q), with_ids(self.p))
        self.assertEqual(q.manifest["procedural"], self.p.manifest["procedural"])
        q.execute("procedural.regenerate", {"id": self.facade})                                 # and the reopened recipe still regenerates
        self.assertEqual(with_ids(q), with_ids(self.p))

    def test_an_sgeo_export_imported_elsewhere_has_the_same_elements(self) -> None:
        from applications.SPADRA import interop as IO
        raw, _report = IO.export_model(self.p, "sgeo")
        q = Project.create("Elsewhere")
        q.execute("model.import", {"format": "sgeo", "name": "p.sgeo", "data": base64.b64encode(raw).decode()})
        self.assertEqual(element_rows(q), element_rows(self.p))

    # ------------------------------------------------------------------------------------------------ no residue
    def test_deleting_a_prototype_leaves_nothing_of_it_and_undo_brings_all_of_it_back(self) -> None:
        before = with_ids(self.p)
        members = set(PG.group(self.p, self.frame)["members"].values())
        self.x("procedural.delete", id=self.frame)
        ids = set(self.p.model.query_ids(limit=10000))
        self.assertFalse(members & ids)
        self.assertNotIn(self.frame, [g["id"] for g in self.p.manifest["procedural"]])
        self.assertFalse([i for i in ids if self.frame in json.dumps(self.p.model.get(i)["metadata"], default=str)])
        self.p.undo()
        self.assertEqual(with_ids(self.p), before)


if __name__ == "__main__":
    unittest.main()
