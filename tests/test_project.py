"""The project layer against the real SLAI SGEO/SDRW implementations: no mocks, real files in a temporary folder."""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA.project import NS, Project
from applications.tools.errors import BadRequest, Conflict, NotFound


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.p = Project.create("Community Pavilion")
        self.plan = next(v["id"] for v in self.p.manifest["views"] if v["kind"] == "plan")
        self.level = self.p.manifest["levels"][0]["id"]

    def line(self, a=(0, 0), b=(6400, 0), **extra):
        return self.p.execute("draw.line", {"drawing": self.plan, "start": a, "end": b, **extra})["result"]["id"]

    def wall(self, a=(0, 0), b=(8400, 0), **extra):
        return self.p.execute("model.add_wall", {"start": a, "end": b, "level": self.level, "thickness": 250, "height": 3.2, **extra})["result"]["id"]


class Creation(ProjectCase):
    def test_new_project_has_real_levels_views_and_empty_documents(self):
        m = self.p.manifest
        self.assertEqual([l["name"] for l in m["levels"]], ["Level 01", "Level 02", "Roof Level"])
        self.assertEqual(sorted(v["kind"] for v in m["views"]), ["3d", "elevation", "elevation", "elevation", "elevation", "plan", "plan"])
        self.assertEqual(sorted(v["name"] for v in m["views"] if v["kind"] == "elevation"), ["East", "North", "South", "West"])
        self.assertEqual(self.p.model.query_ids(), [])
        self.assertEqual(self.p.drawing(self.plan).query_ids(), [])
        self.assertEqual(self.p.revision, 0)

    def test_invalid_unit_and_name_are_refused(self):
        with self.assertRaises(BadRequest):
            Project.create("X", unit="furlong")
        with self.assertRaises(BadRequest):
            Project.create("")


class Drawing2D(ProjectCase):
    def test_line_is_real_sdrw_geometry_and_bumps_revision_once(self):
        eid = self.line()
        g = self.p.drawing(self.plan).get(eid)["geometry"]
        self.assertEqual((g["start"], g["end"]), ([0.0, 0.0], [6400.0, 0.0]))
        self.assertEqual(self.p.revision, 1)

    def test_all_drawing_commands(self):
        d = self.plan
        a = self.line()
        self.p.execute("draw.rectangle", {"drawing": d, "corner": [0, 0], "width": 6400, "height": 4200, "layer": "A-WALL"})
        self.p.execute("draw.circle", {"drawing": d, "center": [3200, 2100], "radius": 500})
        self.p.execute("draw.polyline", {"drawing": d, "points": [[0, 0], [10, 0], [10, 10]], "closed": True})
        dim = self.p.execute("draw.dimension", {"drawing": d, "p1": [0, 0], "p2": [6400, 0], "dimline_point": [0, -600]})["result"]["id"]
        self.p.execute("draw.text", {"drawing": d, "position": [0, 0], "text": "OFFICE"})
        self.assertEqual(self.p.drawing_data(d)["entities"][-1]["kind"], "text")
        data = {e["id"]: e for e in self.p.drawing_data(d)["entities"]}
        self.assertEqual(data[dim]["text"], "6400.00")                     # the dimension value comes from the geometry
        self.p.execute("draw.move", {"drawing": d, "ids": [a], "dx": 100, "dy": 50})
        self.assertEqual(self.p.drawing(d).get(a)["geometry"]["start"], [100.0, 50.0])
        self.p.execute("draw.rotate", {"drawing": d, "ids": [a], "degrees": 90, "about": [0, 0]})
        self.p.execute("draw.scale", {"drawing": d, "ids": [a], "factor": 2})
        copy = self.p.execute("draw.copy", {"drawing": d, "ids": [a], "dx": 1000, "dy": 0})["result"]["ids"]
        self.assertEqual(len(copy), 1)
        self.p.execute("draw.set_layer", {"drawing": d, "ids": [a], "layer": "A-DOOR"})
        self.assertEqual(self.p.drawing(d).get(a)["layer"], "A-DOOR")
        self.p.execute("draw.delete", {"drawing": d, "ids": [a]})
        self.assertNotIn(a, [e["id"] for e in self.p.drawing_data(d)["entities"]])

    def test_dimension_follows_geometry_edits(self):
        a = self.line()
        dim = self.p.execute("draw.dimension", {"drawing": self.plan, "p1": [0, 0], "p2": [6400, 0], "dimline_point": [0, -600]})["result"]["id"]
        self.assertEqual(self.p.drawing(self.plan).dimension_value(dim), 6400.0)

    def test_bad_input_is_refused_and_changes_nothing(self):
        before = self.p.revision
        for params in ({"start": [0, 0], "end": [0, 0]}, {"start": [0, "x"], "end": [1, 1]}, {"start": [float("nan"), 0], "end": [1, 1]},
                       {"start": [0, 0], "end": [1e12, 0]}, {"start": [0, 0]}):
            with self.assertRaises(BadRequest, msg=str(params)):
                self.p.execute("draw.line", {"drawing": self.plan, **params})
        with self.assertRaises(BadRequest):
            self.p.execute("draw.circle", {"drawing": self.plan, "center": [0, 0], "radius": -5})
        with self.assertRaises(NotFound):
            self.p.execute("draw.line", {"drawing": "missing", "start": [0, 0], "end": [1, 1]})
        with self.assertRaises(BadRequest):
            self.p.execute("draw.nonsense", {})
        self.assertEqual(self.p.revision, before)
        self.assertEqual(self.p.drawing(self.plan).query_ids(), [])
        self.assertFalse(self.p.undo_stack)

    def test_failure_midway_through_a_command_restores_the_document(self):
        a = self.line()
        with self.assertRaises(Exception):
            self.p.execute("draw.move", {"drawing": self.plan, "ids": [a, "no-such-id"], "dx": 5, "dy": 5})
        self.assertEqual(self.p.drawing(self.plan).get(a)["geometry"]["start"], [0.0, 0.0])
        self.assertEqual(self.p.revision, 1)


class UndoRedoAndRevisions(ProjectCase):
    def test_undo_redo_round_trip(self):
        a = self.line()
        self.p.execute("draw.move", {"drawing": self.plan, "ids": [a], "dx": 10, "dy": 0})
        self.p.undo()
        self.assertEqual(self.p.drawing(self.plan).get(a)["geometry"]["start"], [0.0, 0.0])
        self.p.redo()
        self.assertEqual(self.p.drawing(self.plan).get(a)["geometry"]["start"], [10.0, 0.0])
        self.p.undo(); self.p.undo()
        self.assertEqual(self.p.drawing(self.plan).query_ids(), [])
        with self.assertRaises(BadRequest):
            self.p.undo()
        self.p.redo()
        self.assertEqual(len(self.p.drawing(self.plan).query_ids()), 1)

    def test_new_command_clears_redo(self):
        self.line(); self.p.undo()
        self.assertTrue(self.p.redo_stack)
        self.line((1, 1), (2, 2))
        self.assertFalse(self.p.redo_stack)

    def test_undo_covers_the_3d_model_and_survives_wall_edits(self):
        w = self.wall()
        self.p.execute("model.set_property", {"id": w, "key": "height", "value": 4.0})
        self.p.undo()
        self.assertAlmostEqual(self.p.model.get(w)["metadata"][f"{NS}:height"], 3.2)
        self.p.undo()
        self.assertEqual(self.p.model.query_ids(), [])

    def test_undo_and_redo_cover_sheets_not_just_documents(self):
        sid = self.p.execute("sheet.create", {"name": "Plan"})["result"]["id"]
        self.p.execute("sheet.set", {"id": sid, "key": "scale", "value": "1:50"})
        self.p.undo()
        self.assertEqual(self.p.properties({"sheet": sid})["top"][3]["value"], "1:100")
        self.p.undo()
        self.assertEqual(self.p.manifest["sheets"], [])
        revision = self.p.revision
        self.p.redo(); self.p.redo()
        self.assertEqual(self.p.manifest["sheets"][0]["scale"], "1:50")
        self.assertGreater(self.p.revision, revision)               # the revision only moves forward, even though the structure went back

    def test_stale_revision_is_rejected_and_nothing_changes(self):
        self.line()
        with self.assertRaises(Conflict):
            self.p.execute("draw.line", {"drawing": self.plan, "start": [5, 5], "end": [6, 6]}, expected_revision=0)
        self.assertEqual(len(self.p.drawing(self.plan).query_ids()), 1)
        self.p.execute("draw.line", {"drawing": self.plan, "start": [5, 5], "end": [6, 6]}, expected_revision=1)

    def test_undo_depth_is_bounded(self):
        p = Project.create("Bounded", undo_depth=3)
        plan = next(v["id"] for v in p.manifest["views"] if v["kind"] == "plan")
        for i in range(10):
            p.execute("draw.line", {"drawing": plan, "start": [0, 0], "end": [i + 1, 0]})
        self.assertEqual(len(p.undo_stack), 3)


class Walls(ProjectCase):
    def test_wall_geometry_derives_from_parameters(self):
        w = self.wall()
        self.assertAlmostEqual(self.p.model.volume(w, unit="m"), 8.4 * 0.25 * 3.2, places=3)
        lo, hi = self.p.model.world_bounds(w)
        self.assertAlmostEqual(hi[0] - lo[0], 8.4, places=3)
        self.assertAlmostEqual(hi[2] - lo[2], 3.2, places=3)
        self.assertAlmostEqual(lo[2], 0.0, places=3)                    # starts at the Level 01 elevation

    def test_editing_each_parameter_regenerates_the_geometry(self):
        w = self.wall()
        self.p.execute("model.set_property", {"id": w, "key": "height", "value": 3.0})
        self.p.execute("model.set_property", {"id": w, "key": "thickness", "value": 300})
        self.p.execute("model.set_property", {"id": w, "key": "length", "value": 5.0})
        self.assertAlmostEqual(self.p.model.volume(w, unit="m"), 5.0 * 0.3 * 3.0, places=3)
        self.p.execute("model.set_property", {"id": w, "key": "level", "value": self.p.manifest["levels"][1]["id"]})
        self.assertAlmostEqual(self.p.model.world_bounds(w)[0][2], 3.2, places=3)       # now stands on Level 02
        props = self.p.properties({"model": w})
        rows = {r["label"]: r["value"] for g in props["groups"] for r in g["rows"]}
        self.assertEqual(rows["Length"], 5.0)
        self.assertEqual(rows["Volume"], "4.500 m³")

    def test_diagonal_wall_keeps_its_length(self):
        w = self.wall((0, 0), (3000, 4000))
        lo, hi = self.p.model.world_bounds(w)
        self.assertAlmostEqual(math.dist(self.p.model.get(w)["metadata"][f"{NS}:start"], self.p.model.get(w)["metadata"][f"{NS}:end"]), 5.0)
        self.assertGreater(hi[0] - lo[0], 3.0)

    def test_move_changes_parameters_not_just_the_box(self):
        w = self.wall()
        self.p.execute("model.move", {"id": w, "dx": 2.0, "dy": 1.0, "dz": 0})
        meta = self.p.model.get(w)["metadata"]
        self.assertEqual(meta[f"{NS}:start"], [2.0, 1.0])
        self.assertAlmostEqual(self.p.model.world_bounds(w)[0][1], 1.0 - 0.125, places=3)

    def test_invalid_wall_edits_are_refused_without_damage(self):
        w = self.wall()
        for key, value in (("height", -1), ("thickness", 0), ("length", 0), ("material", "Unobtainium"), ("level", "nope"), ("color", "red")):
            with self.assertRaises((BadRequest, NotFound), msg=key):
                self.p.execute("model.set_property", {"id": w, "key": key, "value": value})
        self.assertAlmostEqual(self.p.model.volume(w, unit="m"), 8.4 * 0.25 * 3.2, places=3)
        with self.assertRaises(BadRequest):
            self.wall((5, 5), (5, 5))
        with self.assertRaises(NotFound):
            self.p.execute("model.delete", {"id": "ghost"})
        self.assertEqual(len(self.p.model.query_ids()), 1)

    def test_scene_data_is_world_space_triangles_from_sgeo(self):
        w = self.wall()
        scene = self.p.scene_data()
        (obj,) = scene["objects"]
        self.assertEqual(obj["id"], w)
        xs = [v[0] for v in obj["vertices"]]
        self.assertAlmostEqual(max(xs) - min(xs), 8.4, places=3)
        self.assertEqual(len(obj["faces"]), 12)
        self.assertIsNotNone(scene["bounds"])


class Persistence(ProjectCase):
    def build(self):
        self.line(); self.wall()
        self.p.execute("sheet.create", {"name": "Level 01 Plan & Section"})
        self.p.execute("draw.dimension", {"drawing": self.plan, "p1": [0, 0], "p2": [6400, 0], "dimline_point": [0, -600]})

    def test_save_works_where_windows_refuses_to_rename_inside_the_folder(self):
        """A redirected AppData folder (Store Python) answers os.replace with WinError 17 / EXDEV even inside one folder. The save must
        still land, byte for byte, by writing in place; it must not fail with storage_cross_volume."""
        import errno
        from unittest import mock
        self.build()
        target = self.dir / "Redirected.spadra"
        refuse = OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
        with mock.patch("os.replace", side_effect=refuse), mock.patch("time.sleep"):
            self.p.save(target)
        self.assertEqual([f.name for f in target.rglob("*.tmp")], [], "no temporary files are left behind")
        q = Project.open(target)
        self.assertEqual(q.manifest["id"], self.p.manifest["id"])
        self.assertEqual(len(q.drawings), len(self.p.drawings))
        self.assertEqual(q.model.revision, self.p.model.revision)
        self.assertEqual(q.manifest["files"], self.p.manifest["files"])

    def test_save_as_makes_an_independent_copy_and_leaves_the_original_alone(self):
        self.build()
        original = self.dir / "Original.spadra"
        self.p.save(original)
        before = {f.name: f.read_bytes() for f in original.rglob("*") if f.is_file()}
        old_id, old_name = self.p.manifest["id"], self.p.name
        copy = self.dir / "Copy.spadra"
        result = self.p.save_as(copy, "The copy")
        self.assertEqual(Path(result["path"]), copy)
        self.assertEqual((self.p.root, self.p.name, self.p.dirty), (copy, "The copy", False))
        self.assertNotEqual(self.p.manifest["id"], old_id, "a copy is a different project")
        self.line()                                               # keep working: only the copy changes
        self.p.save()
        after = {f.name: f.read_bytes() for f in original.rglob("*") if f.is_file()}
        self.assertEqual(before, after, "the original project is byte-for-byte untouched")
        q = Project.open(copy)
        self.assertEqual((q.name, q.manifest["id"]), ("The copy", self.p.manifest["id"]))
        self.assertEqual((Project.open(original).name, Project.open(original).manifest["id"]), (old_name, old_id))

    def test_save_as_never_overwrites_and_a_failed_copy_changes_nothing(self):
        self.build()
        taken = self.dir / "Taken.spadra"
        self.p.save(taken)
        other = Project.create("Other")
        other.save(self.dir / "Other.spadra")
        name, pid, root = self.p.name, self.p.manifest["id"], self.p.root
        for folder in (taken, self.dir / "Other.spadra"):
            with self.assertRaises(Conflict):
                self.p.save_as(folder, "x")
        self.assertEqual((self.p.name, self.p.manifest["id"], self.p.root), (name, pid, root))
        from unittest import mock
        with mock.patch.object(Project, "save", side_effect=BadRequest("disk")):
            with self.assertRaises(BadRequest):
                self.p.save_as(self.dir / "New.spadra", "Renamed")
        self.assertEqual((self.p.name, self.p.manifest["id"], self.p.root, self.p.dirty), (name, pid, root, self.p.dirty))

    def test_save_close_reopen_restores_everything(self):
        self.build()
        target = self.dir / "Pavilion.spadra"
        self.p.save(target)
        self.assertTrue((target / "project.json").is_file() and (target / "model.sgeo").is_file())
        q = Project.open(target)
        self.assertEqual(q.manifest["id"], self.p.manifest["id"])
        self.assertEqual(sorted(q.drawing(self.plan).query_ids()), sorted(self.p.drawing(self.plan).query_ids()))
        self.assertEqual(sorted(q.model.query_ids()), sorted(self.p.model.query_ids()))
        wid = q.model.query_ids()[0]
        self.assertEqual(q.model.get(wid)["metadata"], self.p.model.get(wid)["metadata"])
        self.assertAlmostEqual(q.model.volume(wid, unit="m"), 8.4 * 0.25 * 3.2, places=3)
        self.assertEqual(q.manifest["sheets"][0]["number"], "A101")
        self.assertEqual(q.warnings, [])
        q.execute("model.set_property", {"id": wid, "key": "height", "value": 3.0})       # and it is still editable after reopening
        q.save()
        self.assertAlmostEqual(Project.open(target).model.volume(wid, unit="m"), 8.4 * 0.25 * 3.0, places=3)

    def test_save_as_never_overwrites_another_project(self):
        target = self.dir / "A.spadra"
        self.p.save(target)
        other = Project.create("Other")
        with self.assertRaises(Conflict):
            other.save(target)
        with self.assertRaises(BadRequest):
            other.save(self.dir / "wrong-suffix")
        with self.assertRaises(BadRequest):
            Project.create("Unsaved").save()
        self.assertEqual(Project.open(target).name, "Community Pavilion")

    def test_open_reports_files_changed_outside_spadra(self):
        target = self.dir / "A.spadra"
        self.line(); self.p.save(target)
        manifest = json.loads((target / "project.json").read_text())
        rel = f"drawings/{self.plan}.sdrw"
        manifest["files"][rel]["sha256"] = "0" * 64
        (target / "project.json").write_text(json.dumps(manifest))
        self.assertTrue(any(rel in w for w in Project.open(target).warnings))

    def test_open_refuses_broken_or_newer_projects(self):
        with self.assertRaises(NotFound):
            Project.open(self.dir / "nothing.spadra")
        target = self.dir / "B.spadra"
        self.p.save(target)
        (target / "model.sgeo").unlink()
        with self.assertRaises(NotFound):
            Project.open(target)
        manifest = json.loads((target / "project.json").read_text())
        manifest["version"] = 99
        (target / "project.json").write_text(json.dumps(manifest))
        with self.assertRaises(BadRequest):
            Project.open(target)
        (target / "project.json").write_text("{not json")
        with self.assertRaises(BadRequest):
            Project.open(target)

    def test_a_failed_save_leaves_the_existing_project_openable(self):
        target = self.dir / "C.spadra"
        self.line(); self.p.save(target)
        good = Project.open(target)
        (target / "drawings").rename(self.dir / "moved")
        (target / "drawings").write_text("not a folder")          # make the next save fail part-way
        with self.assertRaises(Exception):
            self.p.save()
        (target / "drawings").unlink()
        (self.dir / "moved").rename(target / "drawings")
        self.assertEqual(len(Project.open(target).drawing(self.plan).query_ids()), len(good.drawing(self.plan).query_ids()))


class BackupsAndProperties(ProjectCase):
    """Section 19: every save over an existing project keeps the previous version; a backup opens as an unsaved copy; properties are honest."""

    def saved(self, name="B.spadra"):
        self.line()
        target = self.dir / name
        self.p.save(target)
        return target

    def test_first_save_makes_no_backup_and_a_re_save_keeps_the_previous_version(self):
        target = self.saved()
        self.assertEqual(Project.list_backups(target), [])
        before = (target / "model.sgeo").read_bytes(), (target / "project.json").read_bytes()
        self.wall()
        result = self.p.save()
        self.assertIsNotNone(result["backup"])
        self.assertIsNone(result["backup_warning"])
        folder = target / ".backups" / result["backup"]
        self.assertEqual(((folder / "model.sgeo").read_bytes(), (folder / "project.json").read_bytes()), before,
                         "the backup is the previous version on disk, byte for byte (not the version just saved)")
        self.assertNotEqual((target / "model.sgeo").read_bytes(), before[0])
        listed = Project.list_backups(target)
        self.assertEqual([b["id"] for b in listed], [result["backup"]])
        self.assertGreater(listed[0]["bytes"], 0)

    def test_backups_do_not_disturb_opening_and_only_the_newest_five_are_kept(self):
        target = self.saved()
        ids = []
        for _ in range(7):
            self.line(a=(0, len(ids) * 100))
            ids.append(self.p.save()["backup"])
        listed = [b["id"] for b in Project.list_backups(target)]
        self.assertEqual(len(listed), 5)
        self.assertEqual(set(listed), set(ids[-5:]), "the oldest backups are the ones removed")
        self.assertEqual(len(set(ids)), 7, "two saves in the same second still get different backup names")
        again = Project.open(target)
        self.assertEqual(again.manifest["id"], self.p.manifest["id"])
        self.assertEqual([w for w in again.warnings if "backup" in w.lower()], [])

    def test_an_interrupted_or_foreign_folder_is_not_a_backup(self):
        target = self.saved()
        self.wall(); good = self.p.save()["backup"]
        base = target / ".backups"
        (base / "20260101T000000Z-r1").mkdir()                       # no marker: interrupted
        (base / "not-a-backup").mkdir()
        (base / "not-a-backup" / "backup.json").write_text("{}")      # marker but a name that is not ours
        (base / "20260101T000001Z-r2").mkdir()
        (base / "20260101T000001Z-r2" / "backup.json").write_text("{not json")
        self.assertEqual([b["id"] for b in Project.list_backups(target)], [good])
        self.assertEqual(Project.list_backups(None), [])
        self.assertEqual(Project.list_backups(self.dir / "nothing.spadra"), [])

    def test_recover_opens_the_backup_as_a_new_unsaved_copy(self):
        target = self.saved()
        n_before = len(self.p.model.query_ids())
        self.wall()
        backup = self.p.save()["backup"]
        n_after = len(self.p.model.query_ids())
        self.assertGreater(n_after, n_before)
        disk = {f.relative_to(target).as_posix(): f.read_bytes() for f in target.rglob("*") if f.is_file()}
        r = Project.recover(target, backup)
        self.assertEqual(len(r.model.query_ids()), n_before, "the older version came back")
        self.assertIsNone(r.root)
        self.assertTrue(r.dirty)
        self.assertNotEqual(r.manifest["id"], self.p.manifest["id"], "it is a different project, so it can never overwrite the original")
        self.assertTrue(r.name.endswith("(recovered)"))
        self.assertEqual(r.manifest["files"], {})
        with self.assertRaises(BadRequest):
            r.save()                                                  # nowhere to save yet: the person must choose
        self.assertEqual(disk, {f.relative_to(target).as_posix(): f.read_bytes() for f in target.rglob("*") if f.is_file()},
                         "recovering writes nothing to the project or its backups")
        copy = self.dir / "Recovered.spadra"
        r.save(copy)
        self.assertEqual(len(Project.open(copy).model.query_ids()), n_before)

    def test_recover_name_is_not_suffixed_twice_and_stays_within_the_limit(self):
        self.p.manifest["name"] = "N" * 80
        target = self.saved()
        self.wall(); backup = self.p.save()["backup"]
        r = Project.recover(target, backup)
        self.assertLessEqual(len(r.name), 80)
        self.assertTrue(r.name.endswith("(recovered)"))

    def test_a_damaged_backup_is_refused_not_opened(self):
        target = self.saved()
        self.wall(); backup = self.p.save()["backup"]
        folder = target / ".backups" / backup
        data = (folder / "model.sgeo").read_bytes()
        (folder / "model.sgeo").write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(BadRequest) as ctx:
            Project.recover(target, backup)
        self.assertIn("damaged", str(ctx.exception))
        (folder / "model.sgeo").unlink()
        with self.assertRaises(BadRequest):
            Project.recover(target, backup)

    def test_backup_names_are_validated_before_any_path_is_built(self):
        target = self.saved()
        self.wall(); self.p.save()
        for bad in ("../B.spadra", "..", "", "20260101T000000Z-r1/../..", "x/y", "C:\\Windows", "20260101T000000Z-r1\\..", None, 5):
            with self.assertRaises(BadRequest, msg=repr(bad)):
                Project.recover(target, bad)
        with self.assertRaises(NotFound):
            Project.recover(target, "20260101T000000Z-r9")

    def test_a_backup_that_cannot_be_made_does_not_stop_the_save_but_says_so(self):
        from unittest import mock
        target = self.saved()
        self.wall()
        with mock.patch.object(Project, "_backup", side_effect=OSError("disk full")):
            result = self.p.save()
        self.assertIsNone(result["backup"])
        self.assertIn("disk full", result["backup_warning"])
        self.assertFalse(self.p.dirty)
        self.assertEqual(len(Project.open(target).model.query_ids()), len(self.p.model.query_ids()), "the project itself was saved")

    def test_save_as_does_not_copy_backups_and_makes_none_for_a_new_folder(self):
        target = self.saved()
        self.wall(); self.p.save()
        copy = self.dir / "Copy.spadra"
        result = self.p.save_as(copy, "Copy")
        self.assertIsNone(result["backup"])
        self.assertFalse((copy / ".backups").exists())
        self.assertEqual(len(Project.list_backups(target)), 1, "the original keeps its backup")

    def test_project_set_renames_and_describes_and_undo_restores_both(self):
        self.p.execute("project.set", {"name": "Harbour Pavilion", "description": "Phase 2\nwith a second line"})
        self.assertEqual(self.p.name, "Harbour Pavilion")
        self.assertEqual(self.p.manifest["description"], "Phase 2\nwith a second line")
        self.assertTrue(self.p.dirty)
        self.p.undo()
        self.assertEqual(self.p.name, "Community Pavilion")
        self.assertEqual(self.p.manifest.get("description", ""), "")
        self.p.redo()
        self.assertEqual(self.p.name, "Harbour Pavilion")
        self.p.execute("project.set", {"description": ""})                 # clearing is allowed
        self.assertEqual(self.p.manifest["description"], "")

    def test_project_set_rejects_nothing_blank_and_overlong(self):
        for bad in ({}, {"name": ""}, {"name": "   "}, {"name": "x" * 81}, {"description": "y" * 2001}, {"name": 5}):
            before = (self.p.name, self.p.manifest.get("description"), self.p.revision)
            with self.assertRaises(BadRequest, msg=repr(bad)):
                self.p.execute("project.set", bad)
            self.assertEqual((self.p.name, self.p.manifest.get("description"), self.p.revision), before, "a refused edit changes nothing")

    def test_properties_report_what_is_there(self):
        self.line(); self.wall()
        unsaved = self.p.project_properties()
        self.assertEqual((unsaved["path"], unsaved["files"], unsaved["backups"], unsaved["dirty"]), (None, {}, [], True))
        self.assertEqual(unsaved["counts"]["elements"].get("wall"), 1)
        self.assertEqual(unsaved["counts"]["levels"], len(self.p.manifest["levels"]))
        target = self.dir / "P.spadra"
        self.p.save(target)
        self.wall(a=(0, 5000), b=(4000, 5000)); backup = self.p.save()["backup"]
        saved = self.p.project_properties()
        self.assertEqual(saved["path"], str(target))
        self.assertFalse(saved["dirty"])
        self.assertEqual([b["id"] for b in saved["backups"]], [backup])
        self.assertEqual(set(saved["files"]), set(self.p.manifest["files"]))
        for rel, info in saved["files"].items():
            self.assertEqual(info["bytes"], (target / rel).stat().st_size)
            self.assertEqual(len(info["sha256"]), 64)
        self.assertEqual(saved["counts"]["elements"].get("wall"), 2)
        self.assertEqual(saved["backups_kept"], 5)


class PlanOverlay(ProjectCase):
    def test_walls_appear_on_their_level_plan_as_footprints(self):
        w = self.wall()                                           # a wall on Level 01 from the shared fixture
        data = self.p.drawing_data(self.plan)
        self.assertEqual([m["id"] for m in data["model"]], [w])
        poly = data["model"][0]["polygon"]
        self.assertEqual(len(poly), 4)
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        meta = self.p.model.get(w)["metadata"]
        thick_mm = meta[f"{NS}:thickness"] * 1000
        self.assertAlmostEqual(max(ys) - min(ys), thick_mm, places=3)
        self.assertAlmostEqual(max(xs) - min(xs), math.dist(meta[f"{NS}:start"], meta[f"{NS}:end"]) * 1000, places=3)
        self.assertEqual(data["bounds"][0][0], min(xs))           # the plan frames the walls even when nothing is drawn

    def test_other_levels_and_edits_are_respected(self):
        w = self.wall()
        level2 = self.p.manifest["levels"][1]["id"]
        plan2 = next(v["id"] for v in self.p.manifest["views"] if v.get("level_id") == level2)
        self.assertEqual(self.p.drawing_data(plan2)["model"], [])
        self.p.execute("model.set_property", {"id": w, "key": "level", "value": level2})
        self.assertEqual(self.p.drawing_data(self.plan)["model"], [])
        self.assertEqual([m["id"] for m in self.p.drawing_data(plan2)["model"]], [w])
        self.p.execute("model.set_property", {"id": w, "key": "thickness", "value": 400})
        poly = self.p.drawing_data(plan2)["model"][0]["polygon"]
        self.assertAlmostEqual(max(p[1] for p in poly) - min(p[1] for p in poly), 400, places=3)
        self.p.execute("model.delete", {"id": w})
        self.assertEqual(self.p.drawing_data(plan2)["model"], [])


class Sheets(ProjectCase):
    def sheet(self):
        return self.p.execute("sheet.create", {"name": "Level 01 Plan"})["result"]["id"]

    def test_place_a_view_and_read_back_what_to_draw(self):
        a = self.line((100, 200), (900, 200))
        sid = self.sheet()
        vp = self.p.execute("sheet.place_view", {"id": sid, "view_id": self.plan, "x": 50, "y": 60})["result"]["id"]
        data = self.p.sheet_data(sid)
        self.assertEqual((data["width"], data["height"], data["paper"]), (841.0, 594.0, "A1 Landscape"))
        v = data["viewports"][0]
        self.assertEqual((v["id"], v["anchor"], v["scale"], v["mm_per_unit"]), (vp, [100.0, 200.0], "1:100", 1.0))
        self.assertEqual([e["id"] for e in v["entities"]], [a])
        self.assertIn("Viewports (1)", [g["title"] for g in self.p.properties({"sheet": sid})["groups"]])

    def test_a_placed_plan_carries_the_models_walls(self):
        w = self.wall()
        sid = self.sheet()
        self.p.execute("sheet.place_view", {"id": sid, "view_id": self.plan})
        v = self.p.sheet_data(sid)["viewports"][0]
        self.assertEqual([m["id"] for m in v["model"]], [w])
        self.assertEqual(v["anchor"], [0.0, -125.0])                # nothing drawn: anchored at the lower left of what the view shows (the wall's 250 mm outline)

    def test_viewport_edits_and_removal_validate_and_undo(self):
        self.line()
        sid = self.sheet()
        vp = self.p.execute("sheet.place_view", {"id": sid, "view_id": self.plan})["result"]["id"]
        self.p.execute("sheet.set_viewport", {"id": sid, "viewport": vp, "key": "scale", "value": "1:50"})
        self.assertEqual(self.p.sheet_data(sid)["viewports"][0]["scale"], "1:50")
        for bad in ({"key": "scale", "value": "1:7"}, {"key": "colour", "value": 1}, {"key": "x", "value": "wide"}):
            with self.assertRaises(BadRequest):
                self.p.execute("sheet.set_viewport", {"id": sid, "viewport": vp, **bad})
        self.assertEqual(self.p.sheet_data(sid)["viewports"][0]["scale"], "1:50")
        with self.assertRaises(NotFound):
            self.p.execute("sheet.place_view", {"id": sid, "view_id": "not-a-view"})
        self.p.execute("sheet.remove_viewport", {"id": sid, "viewport": vp})
        self.assertEqual(self.p.sheet_data(sid)["viewports"], [])
        self.p.undo()
        self.assertEqual(len(self.p.sheet_data(sid)["viewports"]), 1)

    def test_paper_sizes_follow_orientation(self):
        from applications.SPADRA.project import paper_size
        self.assertEqual(paper_size("A3 Landscape"), (420.0, 297.0))
        self.assertEqual(paper_size("A4 Portrait"), (210.0, 297.0))


class TreeAndChecks(ProjectCase):
    def test_tree_reflects_actual_project_content(self):
        w = self.wall()
        self.p.execute("sheet.create", {"name": "Plan"})
        tree = {s["id"]: s for s in self.p.tree()["sections"]}
        plans = tree["views"]["children"][1]["children"]
        self.assertEqual([n["label"] for n in plans], ["Level 01", "Level 02"])
        walls = next(c for c in tree["categories"]["children"] if c["label"] == "Walls")["children"]
        self.assertEqual([n["id"] for n in walls], [w])
        self.assertEqual(tree["sheets"]["children"][0]["label"], "A101 - Plan")

    def test_check_finds_real_problems_and_nothing_when_clean(self):
        w = self.wall()
        self.assertTrue(self.p.check()["ok"])
        self.p.model.attach_semantics(w, {"level": "deleted-level"}, namespace=NS)           # corrupt it the way a bad edit or merge could
        issues = self.p.check()["issues"]
        self.assertTrue(any("level that no longer exists" in i["message"] for i in issues))

    def test_sheet_numbers_are_unique_and_editable_values_validated(self):
        sid = self.p.execute("sheet.create", {"number": "A101"})["result"]["id"]
        with self.assertRaises(Conflict):
            self.p.execute("sheet.create", {"number": "A101"})
        self.p.execute("sheet.set", {"id": sid, "key": "scale", "value": "1:50"})
        with self.assertRaises(BadRequest):
            self.p.execute("sheet.set", {"id": sid, "key": "scale", "value": "1:3"})
        self.assertEqual(self.p.properties({"sheet": sid})["top"][3]["value"], "1:50")


if __name__ == "__main__":
    unittest.main()
