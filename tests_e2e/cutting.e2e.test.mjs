// End-to-end tests of void cutting control (the Cutting and Diagnostics sections of a void in Properties), in a real browser against the real server.
//   node --test tests_e2e/cutting.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const SLAI = resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir;
const problems = [];

const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-el-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: SLAI, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  return new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`SPADRA did not start:\n${out}`)), 20000);
    server.stdout.on("data", (d) => { out += d; const m = /Open\s*:\s*(http\S+)/.exec(out); if (m) { clearTimeout(timer); ok(m[1]); } });
    server.stderr.on("data", (d) => { out += d; });
    server.on("exit", (code) => { clearTimeout(timer); fail(new Error(`SPADRA exited (${code}):\n${out}`)); });
  });
}

const api = (path, init) => page.evaluate(async ([p, i]) => {
  const r = await fetch(`/api${p}`, { ...(i ?? {}), headers: { "X-SLAI-Spadra": "1", "Content-Type": "application/json" }, credentials: "same-origin" });
  const body = await r.json();
  if (!body.ok) throw new Error(body.error?.message ?? "request failed");
  return body.data;
}, [path, init]);

const settle = () => page.waitForTimeout(350);
const scene = async () => (await api("/scene")).objects;
const ofCategory = async (c) => (await scene()).filter((o) => o.category === c);
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);
const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
const bounds = (o) => [0, 1, 2].map((i) => [Math.min(...o.vertices.map((v) => v[i])), Math.max(...o.vertices.map((v) => v[i]))]);
const spanXY = (o) => { const b = bounds(o); return Math.hypot(b[0][1] - b[0][0], b[1][1] - b[1][0]); };

// 3D: click where a world point (metres) is on screen, using the view's own projection.
const overlay = () => page.locator(".view-overlay").first();
const click3d = async (p) => {
  const box = await overlay().boundingBox();
  const s = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), p);
  assert.ok(s, `point ${p} is on screen`);
  await page.mouse.move(box.x + s[0], box.y + s[1], { steps: 3 });
  await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
};
const clickFraction = async (fx, fy) => {
  const b = await overlay().boundingBox();
  await page.mouse.move(b.x + b.width * fx, b.y + b.height * fy, { steps: 3 });
  await page.mouse.click(b.x + b.width * fx, b.y + b.height * fy); await settle();
};
/** The axis of a wall mesh: its centre line end points on the floor, and the length. */
const axisOf = (wall) => {
  const bottom = wall.vertices.filter((v) => Math.abs(v[2]) < 1e-9);
  let best = [bottom[0], bottom[1]], far = -1;
  for (const a of bottom) for (const b of bottom) { const d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (d > far) { far = d; best = [a, b]; } }
  const [a, b] = best, cx = bottom.reduce((n, v) => n + v[0], 0) / bottom.length, cy = bottom.reduce((n, v) => n + v[1], 0) / bottom.length;
  return { cx, cy, ux: (b[0] - a[0]) / far, uy: (b[1] - a[1]) / far, length: far };
};
const onWall = (ax, f, z) => [ax.cx + ax.ux * (f - 0.5) * ax.length, ax.cy + ax.uy * (f - 0.5) * ax.length, z];

// plan: world (drawing) coordinates to screen, after Zoom to fit
const planScreen = async (d) => {
  await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
  const box = await page.locator(".view-canvas").first().boundingBox();
  const [lo, hi] = d.bounds;
  const k = Math.min((box.width - 128) / (hi[0] - lo[0]), (box.height - 128) / (hi[1] - lo[1]));
  return ([wx, wy]) => [box.x + box.width / 2 + (wx - (lo[0] + hi[0]) / 2) * k, box.y + box.height / 2 - (wy - (lo[1] + hi[1]) / 2) * k];
};
const mean = (poly) => poly.reduce((a, p) => [a[0] + p[0] / poly.length, a[1] + p[1] / poly.length], [0, 0]);

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  if (process.env.SPADRA_DEBUG) page.on("response", async (r) => { if (r.status() >= 400) console.error("HTTP", r.status(), r.request().method(), r.url().split("/api")[1], r.request().postData()?.slice(0, 160), (await r.text()).slice(0, 200)); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const cmd = async (op, params) => (await api("/command", { method: "POST", body: JSON.stringify({ op, params }) })).result;
const rowsOf = async (id) => { const p = await props(id); return { top: p.top, groups: p.groups, all: p.groups.flatMap((g) => g.rows).concat(p.top) }; };
const volumeOf = (o) => { let v = 0; for (const f of o.faces) { const [a, b, c] = f.map((i) => o.vertices[i]); v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6; } return Math.abs(v); };

describe("void cutting control in a browser", () => {
  let level, floorId, voidId;
  it("a void cuts the floor it lies in, and Properties shows Geometry, Cutting and Diagnostics", async () => {
    level = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
    floorId = (await cmd("model.add_floor", { level, points: [[0, 0], [6000, 0], [6000, 5000], [0, 5000]], thickness: 200 })).id;
    voidId = (await cmd("model.add_extrusion", { level, points: [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]], void: true })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await page.getByLabel("Search the project").fill("Void"); await settle();
    await page.getByRole("treeitem", { name: "Void", exact: true }).first().click(); await settle();
    await page.getByLabel("Search the project").fill(""); await settle();
    for (const name of ["Geometry", "Cutting", "Diagnostics"]) await page.locator(".group-head", { hasText: name }).first().waitFor();
    const floor = () => ofCategory("floor").then((l) => l[0]);
    assert.ok(Math.abs(volumeOf(await floor()) - 5.8) < 1e-6, "the void took 1 m2 x 200 mm out of the 6 m3 slab");
    await page.getByText(/1 cut, 0 not supported, 0 left alone/).waitFor();
  });

  it("turning Cut Intersecting Geometry off in Properties gives the floor back whole, and on cuts it again", async () => {
    const select = page.getByLabel("Cut Intersecting Geometry", { exact: true });
    await select.selectOption("Disabled"); await settle();
    const floor = async () => (await ofCategory("floor"))[0];
    assert.ok(Math.abs(volumeOf(await floor()) - 6.0) < 1e-6, "the floor is whole");
    await page.getByText(/0 cut, 0 not supported, 1 left alone/).waitFor();
    await select.selectOption("Enabled"); await settle();
    assert.ok(Math.abs(volumeOf(await floor()) - 5.8) < 1e-6, "cut again");
  });

  it("Selected Targets cuts only what is chosen, and a Beam it reaches is reported as not cut", async () => {
    await cmd("model.add_beam", { level, start: [500, 1500], end: [5500, 1500], top: 2.5 });
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await page.getByLabel("Search the project").fill("Void"); await settle();
    await page.getByRole("treeitem", { name: "Void", exact: true }).first().click(); await settle();
    await page.getByLabel("Search the project").fill(""); await settle();
    await page.getByText(/1 cut, 1 not supported/).waitFor();
    assert.ok((await rowsOf(voidId)).all.some((r) => /^Beam/.test(r.label) && /^Not cut: cutting a structural is not supported/.test(String(r.value))), "the beam is reported, not cut");
    assert.ok(JSON.stringify(await api("/check")).includes("not supported"), "the model check names the uncut beam");
    await page.getByLabel("Cutting policy", { exact: true }).selectOption("targets"); await settle();
    const floor = async () => (await ofCategory("floor"))[0];
    assert.ok(Math.abs(volumeOf(await floor()) - 6.0) < 1e-6, "no target is chosen yet");
    await page.getByLabel("Add a target", { exact: true }).selectOption({ label: "Floor (floor)" }); await settle();
    assert.ok(Math.abs(volumeOf(await floor()) - 5.8) < 1e-6, "the chosen floor is cut");
    assert.ok(!JSON.stringify(await api("/check")).includes("not supported"), "the beam is no longer a target the void acts on, so it is left alone, not reported as unsupported");
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
