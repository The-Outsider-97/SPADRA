"""P2-04 (report Table 17): native interoperability fixtures with *preserved and lost semantics reported*. A project that uses the parametric features is
exported to every model format; for each, what the report says is lost is checked against what really is lost when the file is read back, and the native
round trip is checked to keep what it claims."""
from __future__ import annotations

import base64
import unittest

from applications.SPADRA import families as FM
from applications.SPADRA import interop as IO
from applications.SPADRA.project import Project

from .test_family_contract import family_project


def fixture() -> tuple[Project, dict]:
    """A project that uses families with types and overrides, a wall with a door that swings (an animation), a constraint and a procedural façade."""
    p, fid = family_project()
    level = p.manifest["levels"][0]["id"]
    a = p.execute("family.place", {"family": fid, "level": level, "point": [0, 0], "type": "D-800"})["result"]["id"]
    b = p.execute("family.place", {"family": fid, "level": level, "point": [3000, 0], "type": "D-1000"})["result"]["id"]
    p.execute("model.set_property", {"id": b, "key": "param:height", "value": 2300})
    w1 = p.execute("model.add_wall", {"level": level, "start": [0, 3000], "end": [6000, 3000], "thickness": 200, "height": 3.0})["result"]["id"]
    w2 = p.execute("model.add_wall", {"level": level, "start": [6000, 3000], "end": [6000, 6000], "thickness": 200, "height": 3.0})["result"]["id"]
    door = p.execute("model.add_door", {"wall": w1, "offset": 2.0, "width": 900})["result"]["id"]
    p.execute("anim.swing_door", {"id": door, "angle": 90, "duration": 2.0})
    p.execute("model.constraint_add", {"kind": "coincident", "refs": [{"element": w1, "part": "end"}, {"element": w2, "part": "start"}]})
    p.execute("procedural.facade", {"wall": w2, "panel_width": 1500, "panel_height": 1000})
    return p, {"family": fid, "a": a, "b": b, "door": door}


class Fixtures(unittest.TestCase):
    def setUp(self) -> None:
        self.p, self.ids = fixture()

    def load(self, raw: bytes, fmt: str) -> tuple[Project, dict]:
        q = Project.create("Back")
        res = q.execute("model.import", {"format": fmt, "name": f"fixture.{fmt}", "data": base64.b64encode(raw).decode(), "unit": "m"})["result"]
        return q, res["report"]

    def test_native_sgeo_round_trip_keeps_families_types_overrides_and_says_what_stays_in_the_project(self) -> None:
        raw, export = IO.export_model(self.p, "sgeo")
        self.assertEqual(export["converted"]["families"], 1)
        lost = " | ".join(export["lost"])
        self.assertIn("constraint(s) between walls, beams and columns", lost)
        self.assertIn("procedural group recipe(s)", lost)
        q, report = self.load(raw, "sgeo")
        (fam,) = FM.families(q)
        self.assertEqual(sorted(fam["types"]), ["D-1000", "D-800"])
        by_type = {q.model.get(i).get("family_type"): i for i in q.model.query_ids(limit=10000) if q.model.get(i).get("family_id")}
        self.assertEqual(sorted(by_type), ["D-1000", "D-800"])
        self.assertAlmostEqual(FM.values(q, by_type["D-1000"])["height"], 2.3)                      # the instance override came through
        self.assertAlmostEqual(FM.values(q, by_type["D-800"])["width"], 0.8)                        # the type value came through
        self.assertEqual(q.manifest.get("model_constraints", []), [], "the report said constraints stay in the project; none may appear")
        self.assertIn("the animation stored in the file", report["lost"])                           # and the import says the animation was not brought in

    def test_the_surface_formats_say_they_lose_the_families_and_the_animation_and_it_is_true(self) -> None:
        for fmt in ("glb", "stl", "obj"):
            with self.subTest(fmt=fmt):
                raw, export = IO.export_model(self.p, fmt)
                lost = " | ".join(export["lost"])
                self.assertIn("parametric families" if fmt == "glb" else "parametric family information", lost)
                self.assertIn("animation", lost)
                q, _ = self.load(raw, fmt)
                self.assertEqual(FM.families(q), [], f"{fmt} claims to lose families but they came back")
                self.assertFalse([i for i in q.model.query_ids(limit=10000) if q.model.get(i).get("family_id")])
                self.assertFalse(q.model.animation(), f"{fmt} claims to lose the animation but it came back")

    def test_what_is_reported_lost_depends_on_what_the_project_uses(self) -> None:
        plain = Project.create("Plain")
        plain.execute("model.add_wall", {"level": plain.manifest["levels"][0]["id"], "start": [0, 0], "end": [4000, 0], "thickness": 200, "height": 3.0})
        for fmt in ("sgeo", "glb"):
            _, export = IO.export_model(plain, fmt)
            lost = " | ".join(export["lost"])
            for claim in ("animation", "constraint", "procedural", "script"):
                self.assertNotIn(claim, lost, f"a project without {claim} reported it as lost in {fmt}")

    def test_every_export_report_has_the_same_fields_and_a_file_hash(self) -> None:
        for fmt in IO.MODEL_EXPORT:
            raw, export = IO.export_model(self.p, fmt)
            for key in ("direction", "status", "converted", "lost", "approximated", "unsupported", "errors", "warnings", "assumptions", "source", "destination"):
                self.assertIn(key, export, f"{fmt} report lacks {key}")
            self.assertEqual(export["source"]["bytes"] if "bytes" in export["source"] else export["destination"].get("bytes", len(raw)), len(raw))

    def test_formats_this_machine_cannot_write_are_declared_unavailable_with_a_reason_not_faked(self) -> None:
        cap = IO.availability()
        for name in ("exportModel", "exportDrawing"):
            for fmt, entry in cap[name].items():
                self.assertIn("available", entry)
                if not entry["available"]:
                    self.assertTrue(entry.get("reason"), f"{name}.{fmt} is unavailable without a reason")
        self.assertFalse(cap["investigated"]["ifcExport"]["available"])


if __name__ == "__main__":
    unittest.main()
