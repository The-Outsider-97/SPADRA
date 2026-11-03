"""Negative and security tests (master prompt 23, 25.6): hostile or broken input is refused with a clear error, changes nothing, and never
reaches a file outside the project. Real server, real HTTP."""
from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from applications.SPADRA.app import Settings, build_server

from .harness import TOKEN, Api, ServerCase


class Hostile(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.api.post("/api/project/new", {"name": "Secure", "unit": "mm"})
        tree = self.api.get("/api/project/tree")
        self.level = tree["levels"][0]["id"]
        self.plan = tree["tree"]["sections"][0]["children"][1]["children"][0]["id"]

    def send(self, method: str, path: str, data: bytes | None = None, *, host: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port)
        headers = {"Host": host or f"127.0.0.1:{self.server.port}", "Cookie": self.api.cookie, "X-SLAI-SPADRA": "1", "Content-Type": "application/json"}
        try:
            conn.request(method, path, data, headers)
            r = conn.getresponse()
            body = r.read()
            return r.status, json.loads(body) if body[:1] == b"{" else {"raw": body[:80].decode("latin-1")}
        finally:
            conn.close()

    def revision(self) -> int:
        return self.api.get("/api/project/tree")["project"]["revision"]

    def test_malformed_oversized_and_wrongly_shaped_requests(self):
        before = self.revision()
        self.assertEqual(self.send("POST", "/api/command", b"{nope")[0], 400)
        self.assertEqual(self.send("POST", "/api/command", b"[1, 2]")[0], 400)
        self.assertEqual(self.send("POST", "/api/command", b'"text"')[0], 400)
        self.assertEqual(self.send("POST", "/api/command", b'{"op": 5, "params": {}}')[0], 400)
        try:
            status, out = self.send("POST", "/api/command", b"{" + b" " * (2 << 20) + b"}")
            self.assertEqual((status, out["error"]["code"]), (413, "limit_exceeded"))
        except (BrokenPipeError, ConnectionResetError):          # the server answered 413 and hung up before the whole body was sent: also a refusal
            pass
        self.assertEqual(self.send("DELETE", "/api/command")[0], 405)
        self.assertEqual(self.send("GET", "/api/command")[0], 405)
        self.assertEqual(self.revision(), before, "none of it changed the project")

    def test_another_host_name_is_refused_so_a_web_page_cannot_rebind_to_the_server(self):
        status, out = self.send("GET", "/api/bootstrap", host="evil.example:80")
        self.assertEqual((status, out["error"]["code"]), (401, "unauthorized"))

    def test_paths_cannot_climb_out_of_the_client_folder(self):
        for path in ("/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/..%2f..%2fetc/passwd", "/js/../../secret", "/js/%2e%2e/%2e%2e/secret"):
            status, out = self.send("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn("root:", json.dumps(out))

    def test_numbers_that_are_not_finite_or_absurdly_large_are_refused(self):
        before = self.revision()
        for value in ("NaN", "Infinity", "-Infinity", "1e999", "1e15"):
            body = ('{"op":"model.add_wall","params":{"start":[0,0],"end":[%s,0],"level":"%s"}}' % (value, self.level)).encode()
            status, out = self.send("POST", "/api/command", body)
            self.assertEqual(status, 400, value)
            self.assertFalse(out["ok"])
        for value in ("true", "null", "[1]", '"abc"'):
            body = ('{"op":"model.add_wall","params":{"start":[0,0],"end":[%s,0],"level":"%s"}}' % (value, self.level)).encode()
            self.assertEqual(self.send("POST", "/api/command", body)[0], 400, value)
        self.assertEqual(self.revision(), before)

    def test_control_characters_and_overlong_text_are_refused(self):
        for name in ("a\x00b", "a\x1bb", "x" * 81):
            self.assertEqual(self.send("POST", "/api/command", json.dumps({"op": "project.set", "params": {"name": name}}).encode())[0], 400, repr(name))
        self.assertEqual(self.send("POST", "/api/project/save", json.dumps({"name": "a\x00b"}).encode())[0], 400)

    def test_garbage_imports_are_refused_not_crashed_on(self):
        def attempt(op: str, params: dict) -> int:
            return self.send("POST", "/api/command", json.dumps({"op": op, "params": params}).encode())[0]
        enc = lambda b: base64.b64encode(b).decode()                         # noqa: E731
        for fmt, junk in (("glb", b"not a glb"), ("glb", b"glTF" + b"\x00" * 20), ("stl", b"\x00" * 10), ("stl", b"solid x\nfacet garbage"), ("ifc", b"ISO-10303-21; garbage")):
            self.assertIn(attempt("model.import", {"format": fmt, "data": enc(junk)}), (400, 424, 501, 503), fmt)
        self.assertEqual(attempt("model.import", {"format": "glb", "data": "***not base64***"}), 400)
        self.assertEqual(attempt("model.import", {"format": "obj", "data": enc(b"v 0 0 0")}), 400)
        self.assertEqual(attempt("draw.import", {"drawing": self.plan, "format": "svg", "data": enc(b"<svg")}), 400)
        self.assertEqual(self.api.get("/api/scene")["objects"], [], "nothing half-imported")

    def test_an_svg_cannot_make_the_server_read_a_file_or_expand_entities(self):
        svg = (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd"><!ENTITY y "boom">]>'
               b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text x="1" y="5">&x;&y;</text></svg>')
        status, out = self.send("POST", "/api/command", json.dumps({"op": "draw.import", "params": {"drawing": self.plan, "format": "svg", "data": base64.b64encode(svg).decode()}}).encode())
        self.assertNotIn("root:", json.dumps(out))
        drawing = json.dumps(self.api.get(f"/api/drawing/{self.plan}"))
        self.assertNotIn("root:", drawing)
        self.assertNotIn("boom", drawing, "entities are not expanded")

    def test_project_paths_must_be_project_folders(self):
        for path in ("/etc", "/etc/passwd", "plain-folder", "../escape", "C:\\Windows", ""):
            status, _ = self.send("POST", "/api/project/open", json.dumps({"path": path, "discard": True}).encode())
            self.assertIn(status, (400, 404), repr(path))
        self.assertEqual(self.send("POST", "/api/project/save", json.dumps({"path": "/tmp/not-a-project-name"}).encode())[0], 400)

    def test_a_damaged_project_is_refused_with_a_reason_and_the_open_project_is_kept(self):
        saved = self.api.post("/api/project/save", {"name": "Good"})["path"]
        broken = Path(self.dir) / "Broken.spadra"
        broken.mkdir()
        (broken / "project.json").write_text("{this is not json")
        status, out = self.send("POST", "/api/project/open", json.dumps({"path": str(broken), "discard": True}).encode())
        self.assertEqual(status, 400)
        self.assertTrue(out["error"]["message"])
        self.assertEqual(self.api.get("/api/project/tree")["project"]["path"], saved, "a failed open leaves the open project open")

    def test_a_stale_revision_never_overwrites_a_newer_edit(self):
        a = self.api
        a.cmd("model.add_wall", {"start": [0, 0], "end": [4000, 0], "level": self.level})
        rev = self.revision()
        a.cmd("model.add_wall", {"start": [0, 1000], "end": [4000, 1000], "level": self.level})
        status, out = a.call("POST", "/api/command", {"op": "model.add_wall", "params": {"start": [0, 2000], "end": [4000, 2000], "level": self.level}, "expected_revision": rev})
        self.assertEqual((status, out["error"]["code"]), (409, "conflict"))
        self.assertEqual(len(a.get("/api/scene")["objects"]), 2)

    def test_no_stack_traces_or_server_paths_leak_in_errors(self):
        status, out = self.send("POST", "/api/command", json.dumps({"op": "model.add_wall", "params": {"start": "x", "end": [1, 1], "level": self.level}}).encode())
        text = json.dumps(out)
        self.assertEqual(status, 400)
        for leak in ("Traceback", "File \"", ".py\"", "/home/"):
            self.assertNotIn(leak, text)


class Unauthenticated(unittest.TestCase):
    def test_every_api_address_needs_the_session_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = root / "dist"
            client.mkdir()
            (client / "index.html").write_text("<!doctype html><title>x</title>")
            settings = Settings(port=0, max_request_bytes=1 << 20, undo_depth=5, max_scene_vertices=1000, recent_projects=5, data_dir=root / "data", config_path=root / "none.yaml")
            server, _ = build_server(settings, TOKEN, root=None, client_dir=client)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            try:
                anon = Api(server.port, signed_in=False)
                for method, path in (("GET", "/api/bootstrap"), ("GET", "/api/project/tree"), ("GET", "/api/scene"), ("GET", "/api/project/properties"), ("GET", "/api/animation"),
                                     ("GET", "/api/export/model"), ("POST", "/api/project/new"), ("POST", "/api/project/recover"), ("POST", "/api/quantities"), ("POST", "/api/undo")):
                    self.assertEqual(anon.call(method, path, {} if method == "POST" else None)[0], 401, path)
                wrong = http.client.HTTPConnection("127.0.0.1", server.port)
                wrong.request("GET", "/?token=" + "x" * 43)
                response = wrong.getresponse(); response.read(); wrong.close()
                self.assertEqual(response.getheader("Set-Cookie"), None, "a wrong token does not sign anyone in")
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
