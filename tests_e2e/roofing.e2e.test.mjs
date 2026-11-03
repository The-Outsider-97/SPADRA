// End-to-end tests of the roof types and slope, gutters with drainage, and steel profiles for beams, in a real browser against the real server.
//   node --test tests_e2e/roofing.e2e.test.mjs        (build first: the server serves dist/)
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

let roofId;

describe("roofs, gutters and profiles in a browser", () => {
  it("changes a roof's type and slope in Properties and the roof really changes", async () => {
    const level = (await api("/project/tree")).levels.find((l) => l.name === "Roof Level").id;
    roofId = (await cmd("model.add_roof", { points: [[0, 0], [8000, 0], [8000, 5000], [0, 5000]], level, roof: "gable", pitch: 30, thickness: 200 })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await page.getByLabel("Search the project").fill("Gable Roof"); await settle();
    await page.getByRole("treeitem", { name: /^Gable Roof/ }).first().click(); await settle();
    await page.getByLabel("Search the project").fill(""); await settle();
    await page.waitForSelector(".prop-title:has-text('Roof')");
    const rise = async () => { const [r] = await ofCategory("roof"); const b = bounds(r); return b[2][1] - b[2][0]; };
    const before = await rise();
    assert.ok(before > 1.3 && before < 1.8, `a 30 degree gable over 5 m is about 1.44 m high plus the slab (${before})`);
    const slope = page.getByLabel("Slope", { exact: true });
    await slope.fill("100"); await slope.press("Enter"); await settle();                    // 100 % is 45 degrees
    const rows = (await props(roofId)).groups.flatMap((g) => g.rows).concat((await props(roofId)).top);
    assert.ok(Math.abs(rows.find((r) => r.key === "pitch").value - 45) < 1e-6, "slope and pitch are the same thing said two ways");
    assert.ok((await rise()) > before + 0.5, "the roof is steeper");
    await page.getByLabel("Roof type", { exact: true }).selectOption("hip"); await settle();
    assert.equal((await props(roofId)).top.find((r) => r.key === "roof").value, "hip");
    assert.equal((await ofCategory("roof")).length, 1);
  });

  it("hangs gutters and a downpipe on the roof from the Gutter dialog, and Drainage reports the flow", async () => {
    await tool("Gutter");
    await page.getByRole("button", { name: "Add gutters" }).click(); await settle();
    const gutters = await ofCategory("gutter");
    assert.equal(gutters.length, 4, `a hip roof sheds water on all four eaves (${(await scene()).map((o) => `${o.category}:${o.name}`).join(", ")})`);
    assert.equal((await ofCategory("downpipe")).length, 4, "one downpipe at the low end of each gutter");
    await tool("Drainage");
    const table = page.getByRole("table", { name: "Rainwater drainage" });
    await table.waitFor();
    assert.equal(await table.locator("tbody tr").count(), 5, "a heading row for the roof and one row for each gutter");
    assert.match(await table.innerText(), /l\/s/);
    await page.getByRole("button", { name: "Done" }).click(); await settle();
    const d = await api("/drainage", { method: "POST", body: JSON.stringify({}) });
    const total = d.roofs[0].gutters.reduce((n, g) => n + g.flow_l_s, 0);
    assert.ok(Math.abs(total - 40 * 0.03) < 1e-6, `the whole plan area (40 m2) times 0.03 l/s per m2 (${total})`);
  });

  it("draws a beam with an IPE profile from the Beam tool", async () => {
    await tool("Beam");
    const profile = page.locator(".hud-group select[aria-label='Profile']");
    const families = await profile.locator("option").allTextContents();
    for (const f of ["HEA", "INP", "IPE", "UPE", "CHS", "SHS", "RHS"]) assert.ok(families.some((t) => t.startsWith(f)), `${f} is offered (${families.join(", ")})`);
    assert.ok(families.some((t) => t.startsWith("L ")), "angles are offered");
    assert.ok(families.some((t) => t.startsWith("C ")), "lipped channels are offered");
    await profile.selectOption("IPE"); await settle();
    const size = page.locator(".hud-group select[aria-label='Size']");
    await size.selectOption({ label: "IPE 200" }); await settle();
    const level = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
    const id = (await cmd("model.add_beam", { start: [0, 7000], end: [6000, 7000], level, top: 3.0, profile: "IPE", size: "IPE 200", material: "Steel" })).id;
    const rows = (await props(id)).groups.flatMap((g) => g.rows).concat((await props(id)).top);
    assert.equal(rows.find((r) => r.key === "size").value, "IPE 200");
    assert.ok(rows.some((r) => /second moment/i.test(r.label)), "the section's properties are shown");
    const [beam] = (await ofCategory("beam")).length ? await ofCategory("beam") : (await scene()).filter((o) => o.id === id);
    const b = bounds(beam);
    assert.ok(Math.abs(b[2][1] - b[2][0] - 0.2) < 1e-6, "an IPE 200 is 200 mm deep");
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, []);
  });
});
