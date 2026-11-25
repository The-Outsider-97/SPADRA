import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ProfilerApi } from "../src/client/profiler_api.js";

/** The routes the server really registers for Profiler and the plug-in list (read from the Python source, so the client cannot drift from it; run from the SPADRA folder, as npm test does). */
const SERVER = [readFileSync("profiler/api.py", "utf8"), readFileSync("routes.py", "utf8")].join("\n");
const ROUTES = [...SERVER.matchAll(/@router\.route\("(GET|POST)", "([^"]+)"\)/g)].map((m) => ({ method: m[1]!, pattern: new RegExp(`^${m[2]!.replace(/\{[^}]+\}/g, "[^/]+")}$`) }));

interface Call { method: string; path: string; body?: unknown }
function recorder(): { calls: Call[]; client: never } {
  const calls: Call[] = [];
  const answer = (method: string) => async (path: string, body?: unknown) => { calls.push({ method, path: `/api${path.split("?")[0]}`, body }); return {}; };
  const client = { get: answer("GET"), post: answer("POST"), put: answer("PUT"), delete: answer("DELETE") } as never;
  return { calls, client };
}

test("every Profiler client call goes to a route the server registers, with the method it expects", async () => {
  const { calls, client } = recorder();
  const api = new ProfilerApi(client);
  const sid = "abc123";
  await api.plugins(); await api.info(); await api.create({ template: "rectangle", name: "P", unit: "mm" }); await api.open({ path: "x.sdrw" }); await api.state(sid);
  await api.op(sid, "line", {}); await api.undo(sid); await api.redo(sid); await api.repeat(sid); await api.save(sid, {}); await api.autosave(sid);
  await api.importFile(sid, { path: "a.svg" }); await api.exportFile(sid, { format: "svg", path: "a.svg" }); await api.report(sid, { path: "r.html", format: "html", purpose: "drafting" });
  await api.check(sid, "extrude"); await api.send(sid, { kind: "extrude", options: {} }); await api.close(sid, false);
  assert.ok(calls.length >= 17);
  for (const c of calls) assert.ok(ROUTES.some((r) => r.method === c.method && r.pattern.test(c.path)), `${c.method} ${c.path} is not a server route`);
});

test("an operation carries its name, its arguments and the wish for a fresh drawing", async () => {
  const { calls, client } = recorder();
  await new ProfilerApi(client).op("s1", "rectangle", { p1: [0, 0], p2: [10, 5] }, true);
  assert.deepEqual(calls[0]!.body, { op: "rectangle", args: { p1: [0, 0], p2: [10, 5] }, view: true });
  await new ProfilerApi(client).op("s1", "regions");
  assert.deepEqual(calls[1]!.body, { op: "regions", args: {} });
});
