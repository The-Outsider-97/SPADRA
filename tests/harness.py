"""A real SPADRA server on a loopback port and a tiny HTTP client that performs the same token handshake a browser does."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from applications.SPADRA.app import Settings, build_server

TOKEN = "t" * 43


class Api:
    def __init__(self, port: int, *, signed_in: bool = True) -> None:
        self.port, self.cookie = port, None
        if signed_in:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", f"/?token={TOKEN}")
            r = conn.getresponse(); r.read()
            self.cookie = r.getheader("Set-Cookie", "").split(";")[0]
            conn.close()

    def raw(self, method: str, path: str, body: Any = None, *, marker: bool = True, origin: str | None = None) -> tuple[int, dict[str, str], bytes]:
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if marker:
            headers["X-SLAI-SPADRA"] = "1"
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, data, headers)
        try:
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def call(self, method: str, path: str, body: Any = None, **kw: Any) -> tuple[int, Any]:
        status, _, raw = self.raw(method, path, body, **kw)
        return status, json.loads(raw)

    def get(self, path: str) -> Any:
        status, out = self.call("GET", path)
        assert status == 200 and out["ok"], (status, out)
        return out["data"]

    def post(self, path: str, body: Any = None) -> Any:
        status, out = self.call("POST", path, body or {})
        assert status in (200, 201) and out["ok"], (status, out)
        return out["data"]

    def cmd(self, op: str, params: dict[str, Any], expected: int | None = None) -> Any:
        return self.post("/api/command", {"op": op, "params": params, **({"expected_revision": expected} if expected is not None else {})})


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        client = Path(self.tmp.name) / "dist"
        client.mkdir()
        (client / "index.html").write_text("<!doctype html><title>SPADRA</title>")
        settings = Settings(port=0, max_request_bytes=1 << 20, undo_depth=50, max_scene_vertices=100_000, recent_projects=5,
                            data_dir=Path(self.tmp.name) / "data", config_path=Path(self.tmp.name) / "none.yaml")
        self.server, self.app = build_server(settings, TOKEN, root=None, client_dir=client)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.api = Api(self.server.port)
        self.dir = Path(self.tmp.name)
