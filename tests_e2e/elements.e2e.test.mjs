// End-to-end tests of the building-element tools (door, window, floor, roof, beam) in 3D and on the plan, with real pointer input.
//   node --test tests_e2e/elements.e2e.test.mjs        (build first: the server serves dist/)
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

let axis;

describe("building elements in a browser", () => {
  it("places a door and a window on a wall by clicking it, and the wall really has openings", async () => {
    await tool("Wall");
    await clickFraction(0.3, 0.62); await clickFraction(0.7, 0.62);
    await page.keyboard.press("Escape");
    const [wall] = await ofCategory("wall");
    assert.equal(wall.faces.length, 12, "a plain wall is a box");
    axis = axisOf(wall);
    assert.ok(axis.length > 3, `wall long enough for the test (${axis.length} m)`);
    await tool("Door");
    await click3d(onWall(axis, 0.3, 1.0));
    await tool("Window");
    await click3d(onWall(axis, 0.7, 1.5));
    const [door] = await ofCategory("door"), [win] = await ofCategory("window");
    assert.ok(door && win, "both elements exist");
    const [wallAfter] = await ofCategory("wall");
    assert.ok(wallAfter.faces.length > 12, "the wall became a mesh with openings");
    assert.ok(Math.abs(spanXY(door) - 0.9) < 0.06, `door is 0.9 m wide (${spanXY(door)})`);
    const [, , [dz0, dz1]] = bounds(door);
    assert.ok(Math.abs(dz0) < 1e-6 && Math.abs(dz1 - 2.1) < 1e-6, "door is 2.1 m tall from the floor");
    const [, , [wz0, wz1]] = bounds(win);
    assert.ok(Math.abs(wz0 - 0.9) < 1e-6 && Math.abs(wz1 - 2.1) < 1e-6, "window sill at 0.9 m, head at 2.1 m");
    assert.equal((await api("/check")).problems?.length ?? 0, 0, "the model check finds nothing wrong");
  });

  it("shows the door's properties when selected, and editing the width changes the geometry", async () => {
    await tool("Select");
    const [door] = await ofCategory("door");
    assert.equal((await props(door.id)).title, "Door");
    await click3d(onWall(axis, 0.3, 1.0));
    await page.waitForSelector(".prop-title:has-text('Door')");
    const width = page.locator("#prop-width");
    await width.fill("1200"); await width.press("Enter"); await settle();
    const [after] = await ofCategory("door");
    assert.ok(Math.abs(spanXY(after) - 1.2) < 0.06, `door is now 1.2 m wide (${spanXY(after)})`);
  });

  it("draws a floor from clicked corners with Enter", async () => {
    await tool("Floor");
    for (const [x, y] of [[0.3, 0.72], [0.7, 0.72], [0.7, 0.9], [0.3, 0.9]]) await clickFraction(x, y);
    await page.keyboard.press("Enter"); await settle();
    const [floor] = await ofCategory("floor");
    assert.ok(floor, "a floor exists");
    assert.equal(floor.faces.length, 12);
    const area = (await props(floor.id)).groups.flatMap((g) => g.rows).find((r) => r.label === "Plan area").value;
    assert.match(area, /^\d+(\.\d+)? m²$/);
    assert.ok(parseFloat(area) > 1);
  });

  it("explains why a pitched roof cannot be built on an irregular outline, and builds nothing", async () => {
    await tool("Roof");
    await page.locator("[data-field='roof.kind'], .hud-group:not([hidden]) select").first().selectOption("gable");
    for (const [x, y] of [[0.3, 0.72], [0.6, 0.72], [0.7, 0.9], [0.3, 0.9]]) await clickFraction(x, y);
    await page.keyboard.press("Enter"); await settle();
    await page.getByText(/pitched roof needs a .*rectangular outline/).first().waitFor();
    assert.equal((await ofCategory("roof")).length, 0);
    // the browser logs the refused request itself; that single, intended entry is consumed here so the final check stays strict
    const refused = problems.filter((p) => p.includes("400 (Bad Request)") && p.includes("/api/command"));
    assert.equal(refused.length, 1, "exactly one refused command");
    problems.splice(problems.indexOf(refused[0]), 1);
  });

  it("builds a flat roof on that same outline instead", async () => {
    await page.locator(".hud-group:not([hidden]) select").first().selectOption("flat");
    await overlay().focus();
    await page.keyboard.press("Enter"); await settle();
    const [roof] = await ofCategory("roof");
    assert.ok(roof, "a flat roof exists");
    assert.equal((await props(roof.id)).title, "Flat Roof");
  });

  it("draws a beam between two clicks", async () => {
    await tool("Beam");
    // Low in the view: the tool's settings panel (the HUD) covers the upper part of this short canvas and swallowed the clicks at 45 % of its height.
    await clickFraction(0.3, 0.8); await clickFraction(0.7, 0.8);
    await page.keyboard.press("Escape");
    const [beam] = await ofCategory("structural");
    assert.ok(beam);
    const [, , [z0, z1]] = bounds(beam);
    assert.ok(Math.abs(z1 - 3.0) < 1e-6 && Math.abs(z1 - z0 - 0.4) < 1e-6, `beam top at 3.0 m, 0.4 m deep (${z0}..${z1})`);
  });

  it("shows the wall cut around the door and window, with the swing, on the plan", async () => {
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    const d = await drawing();
    const walls = d.model.filter((m) => m.category === "wall"), doors = d.model.filter((m) => m.category === "door");
    assert.equal(walls.length, 3, "wall in three pieces: before the door, between door and window, after the window");
    assert.equal(doors.length, 1); assert.equal(doors[0].symbol.kind, "door");
    assert.ok(d.model.some((m) => m.category === "window" && m.symbol));
    assert.ok(d.model.some((m) => m.category === "floor") && d.model.some((m) => m.category === "structural"));
  });

  it("places another door on the plan by clicking the wall, deletes it with Delete, and undo brings it back", async () => {
    const d = await drawing();
    const screen = await planScreen(d);
    const length = (poly) => Math.max(...poly.map((p) => p[0])) - Math.min(...poly.map((p) => p[0])) + Math.max(...poly.map((p) => p[1])) - Math.min(...poly.map((p) => p[1]));
    const widest = d.model.filter((m) => m.category === "wall").sort((a, b) => length(b.polygon) - length(a.polygon))[0];
    const [sx, sy] = screen(mean(widest.polygon));
    await tool("Door");
    await page.mouse.move(sx, sy, { steps: 3 }); await page.mouse.click(sx, sy); await settle();
    assert.equal((await ofCategory("door")).length, 2);
    await tool("Select");
    await page.locator(".view-canvas").first().focus();
    await page.keyboard.press("Delete"); await settle();
    assert.equal((await ofCategory("door")).length, 1, "the new door (still selected) was deleted");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await ofCategory("door")).length, 2, "and comes back with undo");
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, []);
  });
});
