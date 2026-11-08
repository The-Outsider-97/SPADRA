// End-to-end test of walls drawn at an angle: they meet in a mitred corner in the model and on the plan, with real pointer input.
//   node --test tests_e2e/walls.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-wl-"));
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



const area = (poly) => Math.abs(poly.reduce((s, p, k) => s + p[0] * poly[(k + 1) % poly.length][1] - poly[(k + 1) % poly.length][0] * p[1], 0)) / 2;
const key = (p, nd = 3) => `${p[0].toFixed(nd)},${p[1].toFixed(nd)}`;

describe("walls at an angle in a browser", () => {
  it("joins two walls drawn at an angle in one mitred corner in the model", async () => {
    await tool("Wall");
    await clickFraction(0.3, 0.7); await clickFraction(0.55, 0.55); await clickFraction(0.75, 0.75);
    await page.keyboard.press("Escape");
    const walls = await ofCategory("wall");
    assert.equal(walls.length, 2, "two walls were drawn");
    const [a, b] = walls.map(axisOf);
    const cross = Math.abs(a.ux * b.uy - a.uy * b.ux), dot = Math.abs(a.ux * b.ux + a.uy * b.uy);
    assert.ok(cross > 0.3 && dot > 0.05, `the walls meet at an angle that is neither straight nor square (sin ${cross.toFixed(2)}, cos ${dot.toFixed(2)})`);
    const floorPoints = (w) => new Set(w.vertices.filter((v) => Math.abs(v[2]) < 1e-9).map((v) => key(v, 4)));
    const shared = [...floorPoints(walls[0])].filter((p) => floorPoints(walls[1]).has(p));
    assert.equal(shared.length, 2, "the walls share the two corner points of the mitre: no overlap and no gap");
    assert.equal((await api("/check")).problems?.length ?? 0, 0, "the model check finds nothing wrong");
  });

  it("draws that corner as one outline on the plan: shared mitre points and no overlap", async () => {
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    const d = await drawing();
    const pieces = d.model.filter((m) => m.category === "wall");
    assert.equal(pieces.length, 2);
    const [p, q] = pieces.map((m) => m.polygon);
    const shared = p.map((v) => key(v)).filter((k) => q.some((w) => key(w) === k));
    assert.equal(new Set(shared).size, 2, "the two wall outlines meet along the mitre line");
    // overlap check: the union of both outlines on a fine grid has the area of the two outlines added
    const inside = (poly, [x, y]) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) if ((poly[i][1] > y) !== (poly[j][1] > y) && x < (poly[j][0] - poly[i][0]) * (y - poly[i][1]) / (poly[j][1] - poly[i][1]) + poly[i][0]) c = !c; return c; };
    const xs = [...p, ...q].map((v) => v[0]), ys = [...p, ...q].map((v) => v[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), n = 400;
    let both = 0;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) { const pt = [x0 + (i + 0.5) * (x1 - x0) / n, y0 + (j + 0.5) * (y1 - y0) / n]; if (inside(p, pt) && inside(q, pt)) both++; }
    assert.ok(both * (x1 - x0) * (y1 - y0) / (n * n) < 0.001 * (area(p) + area(q)), "the outlines do not overlap");
  });

  it("re-joins the corner when one wall is moved with Move, and undo restores it", async () => {
    const [, w2] = await ofCategory("wall");
    const before = JSON.stringify((await drawing()).model.filter((m) => m.category === "wall").map((m) => m.polygon));
    await api("/command", { method: "POST", body: JSON.stringify({ op: "model.move", params: { id: w2.id, dx: 0.5, dy: 0, dz: 0 } }) });
    const moved = JSON.stringify((await drawing()).model.filter((m) => m.category === "wall").map((m) => m.polygon));
    assert.notEqual(moved, before, "the plan follows the move");
    const [p, q] = (await drawing()).model.filter((m) => m.category === "wall").map((m) => m.polygon);
    assert.ok(area(p) > 0 && area(q) > 0);
    await page.keyboard.press("Control+z"); await settle();
    assert.equal(JSON.stringify((await drawing()).model.filter((m) => m.category === "wall").map((m) => m.polygon)), before, "undo restores the first corner exactly");
  });

  it("Shift + double-click selects both connected walls through their join graph", async () => {
    const canvas = page.locator(".view-canvas").first();
    await tool("Select"); await settle();
    const d = await drawing();
    const first = d.model.find((v) => v.category === "wall");
    assert.ok(first?.axis, "wall centre-line is available");
    const world = first.axis.a.map((v, i) => (v + first.axis.b[i]) / 2);
    const pos = await canvas.evaluate((el, point) => el.spadraView.screenOf(point), world);
    const box = await canvas.boundingBox();
    await page.keyboard.down("Shift");
    await page.mouse.dblclick(box.x + pos[0], box.y + pos[1], { delay: 45 });
    await page.keyboard.up("Shift"); await settle();
    const selected = await canvas.evaluate((el) => el.spadraView.store.selection);
    assert.equal(selected.kind, "model");
    assert.deepEqual(new Set(selected.ids), new Set(d.model.filter((v) => v.category === "wall").map((v) => v.id)));
  });

  it("requires Enter to commit a dimension; double-click alone cannot finalize", async () => {
    const canvas = page.locator(".view-canvas").first();
    await page.getByRole("tab", { name: "Annotate", exact: true }).click(); await settle();
    await tool("Dimension"); await settle();
    const box = await canvas.boundingBox();
    const point = async (p) => {
      const s = await canvas.evaluate((el, v) => el.spadraView.screenOf(v), p);
      return [box.x + s[0], box.y + s[1]];
    };
    const start = await point([800, 400]);
    const end = await point([2600, 400]);
    const offset = await point([2000, 1100]);
    await page.mouse.click(...start); await settle();
    await page.mouse.click(...end); await settle();
    await page.mouse.dblclick(...offset, { delay: 50 }); await settle();
    assert.equal((await drawing()).entities.filter((e) => e.kind === "dimension").length, 0);
    await page.mouse.move(...offset);
    await canvas.focus();
    await page.keyboard.press("Enter"); await settle();
    assert.equal((await drawing()).entities.filter((e) => e.kind === "dimension").length, 1);
    await page.keyboard.press("Escape");
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
