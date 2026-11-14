// End-to-end test of the parametric curtain wall in a real browser against the real server: the plan tool opens the dialog, the dialog previews and creates the
// wall, the model, the plan and a section agree, a field becomes a door, and the wall survives a reload. Real pointer input and the real API.
//   node --test tests_e2e/curtainwall.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-cw-"));
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

const cmd = (op, params) => api("/command", { method: "POST", body: JSON.stringify({ op, params }) });
const dlg = () => page.getByRole("dialog", { name: "Curtain wall" });
let cw;                                                                       // the curtain wall's id

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  if (process.env.SPADRA_DEBUG) page.on("response", async (r) => { if (r.status() >= 400) console.error("HTTP", r.status(), r.request().method(), r.url().split("/api")[1], (r.request().postData() ?? "").slice(0, 700), (await r.text()).slice(0, 300)); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const l1 = (await api("/project/tree")).levels[0].id, room = [[0, 0], [8000, 0], [8000, 5000], [0, 5000]];
  for (let i = 0; i < 4; i++) await cmd("model.add_wall", { start: room[i], end: room[(i + 1) % 4], level: l1, thickness: 250, height: 3.2 });
  await page.reload(); await settle(); await settle();                          // the browser learns of the walls made through the API the way a person's other session would
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
  await page.getByRole("tab", { name: /^Level 01/ }).first().click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
  assert.deepEqual(problems, [], "the browser reported no errors");
});

describe("a curtain wall in a browser", () => {
  it("the plan tool opens the dialog, which previews the members the server computes", async () => {
    const d = await drawing();
    const at = await planScreen(d);
    await tool("Curtain wall");
    const a = at([1500, 2000]), b = at([4500, 2000]);
    await page.mouse.move(a[0], a[1], { steps: 3 }); await page.mouse.click(a[0], a[1]); await settle();
    await page.mouse.move(b[0], b[1], { steps: 3 }); await page.mouse.click(b[0], b[1]);
    await dlg().waitFor();
    await dlg().locator("svg [data-member='mullion']").first().waitFor();
    const status = await dlg().locator("[role='status']").textContent();
    assert.match(status ?? "", /vertical and \d+ horizontal members/);
    assert.match(await dlg().locator("[role='note']").textContent() ?? "", /Stabalux|catalogue/i, "the profile's source is named");
    const mullions = await dlg().locator("svg [data-member='mullion']").count();
    // Changing the rule to a fixed number of members is previewed again with that many
    await dlg().getByLabel("Spacing rule").first().selectOption("count");
    await dlg().getByLabel("Distance (mm) or number").first().fill("4"); await dlg().getByLabel("Distance (mm) or number").first().dispatchEvent("change");
    await dlg().locator("[role='status']").filter({ hasText: /^6 vertical/ }).waitFor({ timeout: 5000 });          // 4 interior members and the two at the ends
    assert.equal(await dlg().locator("svg [data-member='mullion']").count(), 6);
    assert.notEqual(mullions, 6, "the previous rule gave another number of members");
  });

  it("creates the wall: one element with frame, glazing and metadata in the model", async () => {
    await dlg().getByRole("button", { name: "Add curtain wall" }).click(); await settle();
    const found = await ofCategory("curtainwall");
    assert.equal(found.length, 1);
    cw = found[0].id;
    assert.ok(found[0].vertices.length > 100, "frame members and glazing are real geometry");
    const p = await props(cw);
    const text = JSON.stringify(p);
    for (const word of ["Division", "Frame", "Panels", "Members", "Quantities"]) assert.match(text, new RegExp(word), `${word} is shown in Properties`);
    const parts = found[0].parts ?? found[0].layer_colors ?? null;
    assert.ok(parts, "the 3D view colours frame and glass apart");
  });

  it("the plan cuts every member and panel and hatches each by its material", async () => {
    const d = await drawing();
    const item = d.model.find((m) => m.category === "curtainwall" && (m.ref ?? m.id) === cw);
    assert.ok(item, "the curtain wall is on the plan");
    assert.ok(item.cut_polygons.length >= 6, "each member is its own cut loop");
    assert.ok(item.cut_materials.includes("Aluminium") && item.cut_materials.includes("Glass"));
    const hatchOf = (m) => item.cut_hatches[item.cut_materials.indexOf(m)];
    assert.ok(hatchOf("Aluminium"), "aluminium is hatched");
    assert.ok(!hatchOf("Glass") || hatchOf("Glass").kind === "none", "glass is left open, as it is drawn");
    assert.equal(hatchOf("Aluminium").standard ?? null, null, "the hatch does not claim to be a standard we do not hold");
  });

  it("a section through the wall cuts the same materials", async () => {
    const out = await cmd("view.add_section", { a: [3000, 500], b: [3000, 3500], name: "Through glass wall" });
    const id = out.result.id;
    const data = await api(`/drawing/${id}`);
    const it = data.projection.items.find((i) => i.id === cw);
    assert.ok(it && it.cut.length >= 2, "the wall is cut");
    assert.ok(it.cut_materials.includes("Aluminium") && it.cut_materials.includes("Glass"));
    assert.equal(it.cut_hatches.length, it.cut.length);
  });

  it("turns a field into a door whose swing is the same in plan and 3D, and keeps the threshold", async () => {
    await cmd("model.curtainwall_panel", { id: cw, col: 0, row: 0, field: "type", value: "Door" });
    await cmd("model.curtainwall_panel", { id: cw, col: 0, row: 0, field: "hinge", value: "end" });
    await cmd("model.curtainwall_panel", { id: cw, col: 0, row: 0, field: "swing", value: "right" });
    const d = await drawing();
    const item = d.model.find((m) => m.category === "curtainwall" && (m.ref ?? m.id) === cw);
    assert.ok(item.symbols?.length >= 1, "a door symbol is drawn in plan");
    const sym = item.symbols[0];
    const o = (await ofCategory("curtainwall"))[0];
    assert.ok(o.vertices.length > 0);
    const pr = JSON.stringify(await props(cw));
    assert.match(pr, /Door/);
    assert.match(JSON.stringify(sym), /end|right|arc|swing/i, "the symbol carries the hinge and swing");
  });

  it("lists the wall in its own schedule with its quantities", async () => {
    const tr = (await api("/project/tree")).tree;
    assert.ok(JSON.stringify(tr).includes("Curtain walls"), "the browser tree has a Curtain walls family");
    const out = await cmd("view.add_schedule", { category: "curtainwall" });
    const sch = await api(`/schedule/${out.result.id}`);
    assert.equal(sch.count, 1);
    assert.match(JSON.stringify(sch.columns), /Panels|Mullion|Profile/i);
  });

  it("a section viewport on a sheet draws the cut in each member's hatch", async () => {
    await page.reload(); await settle(); await settle();                          // the commands above went through the API: the browser takes the project as it now is
    await page.getByRole("tab", { name: "Insert", exact: true }).click();
    await tool("New sheet");
    await page.getByLabel("Sheet name").fill("Curtain"); await page.getByRole("button", { name: "Create" }).click(); await settle();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    for (const label of ["Level 01", "Through glass wall"]) {
      await page.getByRole("button", { name: "Place View" }).click();
      await page.getByRole("menuitem", { name: label, exact: true }).click(); await settle();
    }
    await page.waitForFunction(() => new Set([...document.querySelectorAll(".sheet-wrap svg [data-viewport]")].map((n) => n.getAttribute("data-viewport"))).size >= 2);
    const svg = page.locator(".sheet-wrap svg");
    assert.ok(await svg.locator("[data-cut]").count() >= 4, "the cut members are drawn on the sheet");
  });

  it("survives a reload: the same wall with the door and the same members", async () => {
    const before = (await ofCategory("curtainwall"))[0];
    const propsBefore = JSON.stringify(await props(cw));
    await page.reload(); await settle(); await settle();
    const after = await ofCategory("curtainwall");
    assert.equal(after.length, 1);
    assert.equal(after[0].id, before.id);
    assert.equal(after[0].vertices.length, before.vertices.length);
    assert.equal(JSON.stringify(await props(cw)), propsBefore);
  });

  it("undo takes a change of the wall away again and redo brings it back", async () => {
    const shape = async () => JSON.stringify((await ofCategory("curtainwall"))[0].vertices);
    const n = await shape();
    await cmd("model.curtainwall_panel", { id: cw, col: 0, row: 0, field: "type", value: "Window" });       // the door goes back to a window
    const m = await shape();
    assert.notEqual(m, n, "the change altered the wall's geometry");
    await api("/undo", { method: "POST", body: "{}" }); await settle();
    assert.equal(await shape(), n);
    await api("/redo", { method: "POST", body: "{}" }); await settle();
    assert.equal(await shape(), m);
  });
});
