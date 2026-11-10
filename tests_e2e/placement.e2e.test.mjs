// End-to-end tests of the wall reference line, wall flip, roof end connections and roof windows, in a real browser against the real server.
//   node --test tests_e2e/placement.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const standardSLAI = resolve(HERE, "..", "..", "..");
const siblingSLAI = resolve(HERE, "..", "..");
const SLAI = existsSync(join(standardSLAI, "applications", "SPADRA")) ? standardSLAI : siblingSLAI;
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
  const b = await page.locator(".view-canvas, .view-overlay").first().boundingBox();
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

before(async () => {
  url = await startServer();
  browser = await chromium.launch({
    ...(process.env.SPADRA_CHROMIUM ? { executablePath: process.env.SPADRA_CHROMIUM } : {}),
    args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  });
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
const rowsOf = async (id) => { const p = await props(id); return [...p.top, ...p.groups.flatMap((g) => g.rows)]; };
const rowVal = async (id, key) => (await rowsOf(id)).find((r) => (r.key ?? r.label) === key)?.value;
const select = async (text) => {
  await page.getByLabel("Search the project").fill(text); await settle();
  await page.getByRole("treeitem", { name: new RegExp(`^${text}`) }).first().click(); await settle();
  await page.getByLabel("Search the project").fill(""); await settle();
};
const planWalls = async () => (await drawing()).model.filter((m) => m.category === "wall");
/** Distance of point p from the infinite line through a and b. */
const distFromLine = (p, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1]; return Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / Math.hypot(dx, dy); };

let wallId, roofId;

describe("wall placement in a browser", () => {
  it("draws a wall on its far face with an offset from the HUD, and the model keeps the reference line", async () => {
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    await tool("Wall");
    await page.getByLabel("Wall reference line").selectOption("far");
    const off = page.getByLabel("Wall offset from the reference line (mm)");
    await off.fill("100"); await off.press("Tab");
    await clickFraction(0.3, 0.6); await clickFraction(0.6, 0.6);
    await page.keyboard.press("Escape"); await settle();
    const walls = await ofCategory("wall");
    assert.equal(walls.length, 1, "one wall was drawn");
    wallId = walls[0].id;
    assert.equal(await rowVal(wallId, "reference"), "far", "Properties shows the reference the wall was drawn with");
    assert.equal(await rowVal(wallId, "reference_offset"), 100);
    const t = await rowVal(wallId, "thickness");
    const [w] = await planWalls();
    assert.ok(w.reference && w.reference.kind === "far", "the plan carries the reference line");
    const gap = distFromLine(w.axis.a, w.reference.a, w.reference.b);
    assert.ok(Math.abs(gap - Math.abs(100 - t / 2)) < 1e-3, `centre = reference + offset + sgn x t/2 along the left normal: far face, +100 mm, t ${t} gives ${Math.abs(100 - t / 2)} mm (got ${gap})`);
  });

  it("changes the thickness in Properties and the reference line stays where it was", async () => {
    const [before] = await planWalls();
    const a0 = before.reference.a.map((x) => x), b0 = before.reference.b.map((x) => x);
    await select("Basic Wall");
    const field = page.getByRole("region", { name: "Properties" }).getByLabel("Thickness", { exact: true });
    const old = await rowVal(wallId, "thickness");
    await field.fill(String(old * 2)); await field.press("Enter"); await settle();
    const [after] = await planWalls();
    for (const i of [0, 1]) { assert.ok(Math.abs(after.reference.a[i] - a0[i]) < 1e-6 && Math.abs(after.reference.b[i] - b0[i]) < 1e-6, "the reference line did not move"); }
    assert.ok(Math.abs(distFromLine(after.axis.a, a0, b0) - distFromLine(before.axis.a, a0, b0)) > 1e-6, "the wall grew from the reference line, not about its centre");
  });

  it("flips the wall from the Properties button: same place, reversed path, near and far swapped", async () => {
    const [before] = await planWalls();
    await select("Basic Wall");
    await page.getByRole("button", { name: /^Flip wall/ }).click(); await settle();
    const [after] = await planWalls();
    assert.equal(await rowVal(wallId, "reference"), "near", "far became near");
    const poly = (p) => [...p.polygon].map((q) => q.map((x) => x.toFixed(4)).join(",")).sort().join(" ");
    assert.equal(poly(after), poly(before), "the wall occupies exactly the same footprint");
    assert.ok(Math.abs(after.axis.a[0] - before.axis.b[0]) < 1e-6 && Math.abs(after.axis.a[1] - before.axis.b[1]) < 1e-6, "the path runs the other way");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal(await rowVal(wallId, "reference"), "far", "undo restores the reference");
  });
});

describe("roof ends and roof windows in a browser", () => {
  it("opens End connections from Properties, previews a preset in section and applies it", async () => {
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    const level = (await api("/project/tree")).levels.find((l) => l.name === "Roof Level").id;
    roofId = (await cmd("model.add_roof", { points: [[0, 0], [8000, 0], [8000, 5000], [0, 5000]], level, roof: "gable", pitch: 30, thickness: 200 })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await select("Gable Roof");
    await page.getByRole("button", { name: "End connections…" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("img", { name: /Section across the eave/ }).waitFor();
    await dialog.getByLabel("Connection").selectOption("C4"); await settle();
    assert.equal(await dialog.getByLabel("Cut", { exact: true }).inputValue(), "horizontal", "C4 is a horizontal cut");
    assert.equal(await dialog.getByLabel("Cut point over the wall line").inputValue(), "top", "C4 is aligned at the upper corner");
    await dialog.getByRole("button", { name: "Apply" }).click(); await settle();
    assert.equal(await rowVal(roofId, "end:0:type"), "C4");
    assert.equal(await rowVal(roofId, "end:0:cut"), "horizontal");
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(!(await rowVal(roofId, "end:0:type")) || (await rowVal(roofId, "end:0:type")) === "default", "undo removes the end condition");
    assert.equal((await api("/check")).problems?.filter((p) => p.severity === "error").length ?? 0, 0, "no check errors");
  });

  it("adds a roof window from Properties and it is real geometry in the model", async () => {
    await select("Gable Roof");
    await page.getByRole("button", { name: "Add roof window" }).click(); await settle();
    const windows = await ofCategory("roof_window");
    assert.equal(windows.length, 1, "one roof window");
    const roof = (await ofCategory("roof"))[0];
    assert.ok(windows[0].vertices.length > 8, "the window has frame and pane geometry");
    assert.ok(roof.faces.length > 0);
    assert.equal(await rowVal(windows[0].id, "Roof"), "Gable Roof", "the window is hosted by the roof");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await ofCategory("roof_window")).length, 0, "undo removes it");
  });

  it("raised no console or page errors", async () => { assert.deepEqual(problems, []); });
});
