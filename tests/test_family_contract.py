"""P0-01 (report Table 16): the typed family contract is versioned, older files migrate, and invalid types or units are refused with the model unchanged.

* A family travels as a native SGEO *family file*. It is written at the current SGEO version and read back (types, bounds, formulas, units) without loss;
  a file written by an older SGEO (0.1.0 and 0.2.0 fixtures assembled the way those writers did) is upgraded in memory and the report says so.
* Importing is a command: one revision, one undo step, nothing half-imported when a file is refused.
* Every invalid type, unit, bound, formula dimension or tampered file leaves the project's full fingerprint (model, drawings, manifest, revision, history) as it was."""
from __future__ import annotations

import io
import json
import unittest
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from applications.SPADRA import families as FM
from applications.SPADRA import interop as IO
from applications.SPADRA.project import Project
from applications.tools.errors import AppError, BadRequest
from src.utils.sgeo import SGEODocument
from src.utils.spatial_formats.common.helpers import canonical_json, digest

from .test_fault_injection import fingerprint


def family_project() -> tuple[Project, str]:
    p = Project.create("Source")
    fid = p.execute("family.define", {"name": "Door", "shape": "box", "parameters": {
        "width": {"default": 900, "min": 600, "max": 1800, "scope": "type"}, "height": {"default": 2100, "min": 1800, "max": 2600},
        "depth": {"default": 40}, "frame": {"default": 35}, "clear_width": {"type": "length", "expression": "width - 2 * frame"},
        "swing": {"type": "angle", "default": 90, "min": 0, "max": 180}, "leaf_mass": {"type": "mass", "default": 25}}})["result"]["id"]
    for name, w in (("D-800", 800), ("D-1000", 1000)):
        p.execute("family.type_define", {"id": fid, "name": name, "values": {"width": w}})
    return p, fid


def legacy_family_package(version: str, schema: str, family_body: dict) -> bytes:
    """A genuine older-version family package: the members and manifest a 0.1/0.2 writer produced (no materials/images/modifiers before 0.2/0.3)."""
    doc = SGEODocument.new("old", unit="m", document_kind="family")
    body = doc.model.as_dict()
    body["families"] = [family_body]
    for key in (("materials", "images", "modifiers") if version == "0.1.0" else ("modifiers",)):
        body.pop(key, None)
    members = {"scene.json": canonical_json(body)}
    manifest = {"format_id": "slai.sgeo", "format_version": version, "schema": schema, "spatial_dimension": 3, "document_kind": "family",
                "document_id": body["document_id"], "revision": body["revision"], "units": "m", "axis_up": "+Z", "handedness": "right",
                "requires": ["scene-graph", "analytic-primitives", "triangle-mesh"],
                "parts": [{"path": n, "size": len(r), "sha256": digest(r)} for n, r in sorted(members.items())]}
    out = io.BytesIO()
    with ZipFile(out, "w", allowZip64=False) as z:
        for name, raw, comp in [("mimetype", b"application/x-slai-sgeo+zip", ZIP_STORED), ("manifest.json", canonical_json(manifest), ZIP_DEFLATED)] + \
                [(n, r, ZIP_DEFLATED) for n, r in sorted(members.items())]:
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = comp
            info.external_attr = 0o100644 << 16
            z.writestr(info, raw)
    return out.getvalue()


def rewrite_member(data: bytes, member: str, change) -> bytes:
    """The same package with one member edited (and the manifest's size/hash left as it was: a tampered file)."""
    out = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as zin, ZipFile(out, "w") as zout:
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename == member:
                raw = change(raw)
            zout.writestr(info, raw)
    return out.getvalue()


class FamilyFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.src, self.fid = family_project()
        self.raw = FM.export_family_file(self.src, self.fid)

    def load(self, project: Project, raw: bytes, name: str = "Door.sgeo") -> dict:
        import base64
        return project.execute("model.import", {"format": "sgeo", "name": name, "data": base64.b64encode(raw).decode()})["result"]

    def test_the_file_declares_its_schema_and_version(self) -> None:
        with ZipFile(io.BytesIO(self.raw)) as z:
            manifest = json.loads(z.read("manifest.json"))
        self.assertEqual((manifest["format_id"], manifest["document_kind"]), ("slai.sgeo", "family"))
        self.assertRegex(manifest["format_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(manifest["schema"], "slai.sgeo/" + ".".join(manifest["format_version"].split(".")[:2]))

    def test_a_family_round_trips_through_the_model_import_command(self) -> None:
        q = Project.create("Target")
        rev, depth = q.revision, len(q.undo_stack)
        result = self.load(q, self.raw)
        self.assertEqual((result["objects"], result["report"]["converted"]["families"], result["report"]["converted"]["family_types"]), (0, 1, 2))
        (fam,) = FM.families(q)
        original = FM.family(self.src, self.fid)
        self.assertEqual(fam["parameters"], original["parameters"])                    # types, bounds, formulas, units, scope: all of it
        self.assertEqual(fam["types"], original["types"])
        self.assertEqual((q.revision, len(q.undo_stack)), (rev + 1, depth + 1))        # one revision, one undo step
        placed = q.execute("family.place", {"family": fam["id"], "level": q.manifest["levels"][0]["id"], "point": [0, 0], "type": "D-1000"})["result"]["id"]
        self.assertEqual(round(FM.values(q, placed)["clear_width"], 6), 0.93)         # the imported formula really evaluates
        q.undo()
        q.undo() if q.undo_stack and FM.families(q) else None
        self.assertEqual(FM.families(q), [])

    def test_importing_again_adds_nothing_and_a_name_clash_is_renamed(self) -> None:
        q = Project.create("Target")
        self.load(q, self.raw)
        again = self.load(q, self.raw)
        self.assertEqual(again["report"]["converted"]["families"], 0)
        self.assertTrue(any("already in the project unchanged" in a for a in again["report"]["assumptions"]))
        other, _ = family_project()
        clash = FM.export_family_file(other, FM.families(other)[0]["id"])              # a different document with the same name, new ids
        twice = self.load(q, clash)
        self.assertEqual(twice["report"]["converted"]["families"], 1)
        self.assertEqual(sorted(f["name"] for f in FM.families(q)), ["Door", "Door 2"])

    def test_older_versions_are_upgraded_in_memory_and_the_report_says_so(self) -> None:
        body = FM.family(self.src, self.fid)
        body.pop("instance_count", None)
        for version, schema in (("0.1.0", "slai.sgeo/0.1"), ("0.2.0", "slai.sgeo/0.2")):
            with self.subTest(version=version):
                legacy = legacy_family_package(version, schema, body)
                self.assertEqual(SGEODocument.from_bytes(legacy).source_version, version)
                q = Project.create("Target")
                result = self.load(q, legacy, name=f"Door-{version}.sgeo")
                self.assertEqual(result["report"]["converted"]["families"], 1)
                self.assertTrue(any(version in a and "upgraded in memory" in a for a in result["report"]["assumptions"]), result["report"]["assumptions"])
                self.assertEqual(FM.families(q)[0]["parameters"], body["parameters"])
                self.assertEqual(FM.families(q)[0]["types"], body["types"])

    def test_a_tampered_or_invalid_file_is_refused_and_the_project_is_unchanged(self) -> None:
        q = Project.create("Target")
        q.execute("family.define", {"name": "Existing", "shape": "box", "parameters": {"width": {"default": 500}, "depth": {"default": 400}, "height": {"default": 700}}})
        before = fingerprint(q)

        def edit_scene(change):
            def apply(raw: bytes) -> bytes:
                scene = json.loads(raw)
                change(scene)
                return json.dumps(scene).encode()
            return apply

        cases = {
            "hash mismatch": rewrite_member(self.raw, "scene.json", lambda raw: raw + b" "),
            "unknown parameter type": rewrite_member(self.raw, "scene.json", edit_scene(lambda s: s["families"][0]["parameters"]["width"].update(type="furlongs"))),
            "not a package": b"PK\x03\x04 this is not a family",
            "wrong kind of bytes": b"{}",
        }
        for label, raw in cases.items():
            with self.subTest(case=label), self.assertRaises(AppError):
                self.load(q, raw)
            self.assertEqual(fingerprint(q), before, label)


class InvalidTypesAndUnits(unittest.TestCase):
    """Typed values are checked where they enter: a wrong type, unit, bound or formula dimension never reaches the model."""

    def setUp(self) -> None:
        self.p, self.fid = family_project()
        self.level = self.p.manifest["levels"][0]["id"]
        self.door = self.p.execute("family.place", {"family": self.fid, "level": self.level, "point": [0, 0]})["result"]["id"]

    def refuse(self, op: str, params: dict, message: str | None = None) -> None:
        before = fingerprint(self.p)
        with self.assertRaises(BadRequest) as ctx:
            self.p.execute(op, params)
        if message:
            self.assertRegex(str(ctx.exception), message)
        self.assertEqual(fingerprint(self.p), before, f"{op} {params} changed the project although it was refused")

    def test_a_definition_with_an_unknown_type_or_a_missing_default_is_refused(self) -> None:
        self.refuse("family.define", {"name": "A", "shape": "box", "parameters": {"width": {"type": "furlongs", "default": 1}}})
        self.refuse("family.define", {"name": "B", "shape": "box", "parameters": {"width": {"type": "length"}}})
        self.refuse("family.define", {"name": "C", "shape": "box", "parameters": {"width": {"type": "length", "default": "wide"}}})
        self.refuse("family.define", {"name": "D", "shape": "box", "parameters": {"width": {"type": "length", "default": True}}})

    def test_a_default_outside_its_own_bounds_is_refused(self) -> None:
        self.refuse("family.define", {"name": "E", "shape": "box", "parameters": {"width": {"default": 5000, "min": 100, "max": 1000}}})
        self.refuse("family.define", {"name": "F", "shape": "box", "parameters": {"width": {"default": 500, "min": 1000, "max": 100}}})

    def test_a_formula_that_mixes_dimensions_is_refused_and_names_the_problem(self) -> None:
        self.refuse("family.update", {"id": self.fid, "parameters": {"clear_width": {"type": "length", "expression": "width + swing"}}},
                    "length|angle|dimension")
        self.refuse("family.update", {"id": self.fid, "parameters": {"clear_width": {"type": "length", "expression": "width * leaf_mass"}}})

    def test_an_instance_value_of_the_wrong_type_or_outside_bounds_is_refused(self) -> None:
        self.refuse("model.set_property", {"id": self.door, "key": "param:width", "value": "wide"})
        self.refuse("model.set_property", {"id": self.door, "key": "param:width", "value": 99999})
        self.refuse("model.set_property", {"id": self.door, "key": "param:clear_width", "value": 800})       # a derived parameter is not overridable
        self.refuse("model.set_property", {"id": self.door, "key": "param:swing", "value": 400})            # beyond 180°
        self.refuse("model.set_property", {"id": self.door, "key": "param:nonexistent", "value": 1})

    def test_a_type_value_of_the_wrong_kind_is_refused(self) -> None:
        self.refuse("family.type_define", {"id": self.fid, "name": "Bad", "values": {"width": "a lot"}})
        self.refuse("family.type_define", {"id": self.fid, "name": "Bad2", "values": {"clear_width": 700}}, "formula")
        self.refuse("family.type_define", {"id": self.fid, "name": "Bad3", "values": {"width": 50}})                  # below the minimum


if __name__ == "__main__":
    unittest.main()
