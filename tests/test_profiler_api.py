"""Profiler end to end over HTTP: real server, real SDRW files on disk, real SPADRA model changes."""
from __future__ import annotations

import json
import unittest

from . import profiler_env  # noqa: F401  (must come before anything saves an .sdrw)
from .harness import ServerCase


class ProfilerApi(ServerCase):
    def new(self, template="rectangle", **kw):
        return self.api.post("/api/profiler/new", {"template": template, **kw})

    def op(self, sid, op, **args):
        return self.api.post("/api/profiler/%s/op" % sid, {"op": op, "args": args})

    def test_registry_is_honest(self):
        items = {i["id"]: i for i in self.api.get("/api/plugins")["plugins"]}
        self.assertEqual(items["profiler"]["state"], "ready")
        for k in ("modeller", "modelpainter", "presentator"):
            self.assertEqual(items[k]["state"], "missing", k)
            self.assertIn("not part of this installation", items[k]["reason"])
        self.assertEqual(items["cadcon"]["state"], "separate")
        self.assertEqual(self.api.get("/api/plugins")["ribbons"], {"Plug-ins": ["profiler", "modeller", "cadcon", "modelpainter"], "Editor": ["animation", "presentator"]})

    def test_parametric_rectangle_and_formula_numbers(self):
        v = self.new()
        sid = v["status"]["id"]
        self.assertEqual(v["diagnostics"]["status"], "full")
        out = self.op(sid, "param_set", name="Width", value=150)
        regs = self.op(sid, "regions")["result"]["regions"]
        self.assertAlmostEqual(regs[0]["area"], 150 * 100, 6)
        self.assertTrue(out["changed"])
        # undo restores the exact previous geometry
        self.api.post("/api/profiler/%s/undo" % sid)
        regs = self.op(sid, "regions")["result"]["regions"]
        self.assertAlmostEqual(regs[0]["area"], 120 * 100, 6)

    def test_save_open_roundtrip_and_conflict(self):
        sid = self.new("angle")["status"]["id"]
        path = str(self.dir / "angle.sdrw")
        saved = self.api.post("/api/profiler/%s/save" % sid, {"path": path})
        self.assertTrue(saved["path"].endswith("angle.sdrw"))
        self.op(sid, "param_set", name="Width", value=130)
        self.api.post("/api/profiler/%s/save" % sid, {"overwrite": True})
        o = self.api.post("/api/profiler/open", {"path": path})
        regs = self.op(o["status"]["id"], "regions")["result"]["regions"]
        self.assertAlmostEqual(regs[0]["area"], 130 * 12 + 138 * 12, 6)
        # closing with unsaved changes is refused, discarding closes it
        self.op(o["status"]["id"], "param_set", name="Width", value=90)
        status, body = self.api.call("POST", "/api/profiler/%s/close" % o["status"]["id"], {})
        self.assertEqual(status, 409)
        self.assertEqual(self.api.post("/api/profiler/%s/close" % o["status"]["id"], {"discard": True})["closed"], o["status"]["id"])

    def test_send_extrusion_with_hole_and_resync(self):
        project = self.api.post("/api/project/new", {"name": "P", "unit": "mm"})
        sid = self.new("blank")["status"]["id"]
        self.op(sid, "rectangle", p1=[0, 0], p2=[1000, 600])
        self.op(sid, "circle", center=[500, 300], radius=100)
        res = self.api.post("/api/profiler/%s/send" % sid, {"kind": "extrude", "options": {"depth": 0.3, "mode": "embedded"}})
        self.assertEqual(len(res["result"]["ids"]), 2)                       # the solid and the hole's companion void
        self.assertFalse(res["closed"])                            # Upload keeps Profiler open
        # Upload and Close closes only after the model accepted it
        res2 = self.api.post("/api/profiler/%s/send" % sid, {"kind": "extrude", "options": {"depth": 0.3}, "close": True})
        self.assertTrue(res2["closed"])
        self.assertEqual(self.api.call("GET", "/api/profiler/%s" % sid)[0], 404)

    def test_unsuitable_profile_is_refused_and_model_untouched(self):
        self.api.post("/api/project/new", {"name": "P", "unit": "mm"})
        before = self.api.get("/api/bootstrap")["project"]["revision"]
        sid = self.new("blank")["status"]["id"]
        self.op(sid, "polyline", points=[[0, 0], [10, 0], [10, 10], [0, 10]])
        status, body = self.api.call("POST", "/api/profiler/%s/send" % sid, {"kind": "extrude"})
        self.assertEqual(status, 400)
        self.assertIn("not suitable", body["error"]["message"] if "error" in body else json.dumps(body))
        self.assertEqual(self.api.get("/api/bootstrap")["project"]["revision"], before)
        self.assertEqual(self.api.call("POST", "/api/profiler/%s/send" % sid, {"kind": "nonsense"})[0], 400)

    def test_autosave_and_recovery(self):
        sid = self.new("rectangle")["status"]["id"]
        self.op(sid, "param_set", name="Width", value=140)
        a = self.api.post("/api/profiler/%s/autosave" % sid)
        self.assertIsNotNone(a["autosaved"])
        info = self.api.get("/api/profiler")
        self.assertEqual(len(info["recoverable"]), 1)
        rec = self.api.post("/api/profiler/open", {"recover": info["recoverable"][0]["path"]})
        regs = self.op(rec["status"]["id"], "regions")["result"]["regions"]
        self.assertAlmostEqual(regs[0]["area"], 140 * 100, 6)
        self.assertTrue(rec["status"]["dirty"])


if __name__ == "__main__":
    unittest.main()
