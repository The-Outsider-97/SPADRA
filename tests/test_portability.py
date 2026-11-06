"""What differs between the systems SPADRA runs on, and between a developer's machine and a user's: names and folders with spaces and accents,
and the text encoding a file is written in (Windows opens text files in a legacy code page unless told otherwise)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from applications.SPADRA.project import Project

NAME = "Café — Œuvre 住宅 ☃"


class SavedProjects(unittest.TestCase):
    def test_a_project_with_accented_names_in_an_accented_folder_saves_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Mijn projecten — één" / "Dossier Café.spadra"
            a = Project.create(NAME)
            level = a.manifest["levels"][0]["id"]
            a.execute("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": level, "thickness": 200, "height": 3, "name": NAME})
            a.save(folder)
            b = Project.open(folder)
            self.assertEqual(b.manifest["name"], NAME)
            self.assertEqual([o["name"] for o in b.scene_data()["objects"]], [o["name"] for o in a.scene_data()["objects"]])

    def test_the_manifest_is_utf8_whatever_the_system_code_page_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "p.spadra"
            Project.create(NAME).save(folder)
            raw = (folder / "project.json").read_bytes()
            self.assertEqual(json.loads(raw.decode("utf-8"))["name"], NAME)       # decoding as UTF-8 must work and give the name back


if __name__ == "__main__":
    unittest.main()
