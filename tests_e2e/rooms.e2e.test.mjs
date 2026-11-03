// End-to-end tests of rooms and columns in a real browser against the real server: the tools with real pointer input, the plan drawing,
// the Properties panel, the room schedule and its CSV.
//   SPADRA_E2E_ROOT=<a SLAI root> node --test tests_e2e/rooms.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.SPADRA_E2E_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir, level, walls = [];
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-room-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  return new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`SPADRA did not start:\n${out}`)), 30000);
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
const cmd = async (op, params) => (await api("/command", { method: "POST", body: JSON.stringify({ op, params }) })).result;
const settle = () => page.waitForTimeout(450);
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
// Room and Column are on the Insert tab (the Model tab keeps the layout of the design); Escape returns to the Select tool
const insertTool = async (name) => { await page.getByRole("tab", { name: "Insert", exact: true }).click(); await tool(name); };
const backToSelect = async () => { await page.keyboard.press("Escape"); await settle(); };
const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const rowOf = (p, label) => [...p.top, ...p.groups.flatMap((g) => g.rows)].find((r) => r.label === label);
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);
const canvas = () => page.locator(".view-canvas").first();
const model = async (category) => (await drawing()).model.filter((m) => m.category === category);

// plan: drawing coordinates to screen, after Zoom to fit
const planScreen = async () => {
  await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
  const d = await drawing();
  const box = await canvas().boundingBox();
  const [lo, hi] = d.bounds;
  const k = Math.min((box.width - 128) / (hi[0] - lo[0]), (box.height - 128) / (hi[1] - lo[1]));
  return ([wx, wy]) => [box.x + box.width / 2 + (wx - (lo[0] + hi[0]) / 2) * k, box.y + box.height / 2 - (wy - (lo[1] + hi[1]) / 2) * k];
};
const clickAt = async ([sx, sy]) => { await page.mouse.move(sx, sy, { steps: 3 }); await page.mouse.click(sx, sy); await settle(); };

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  level = (await api("/project/tree")).levels[0].id;
  const box = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]];
  for (let i = 0; i < 4; i++) walls.push((await cmd("model.add_wall", { start: box[i], end: box[(i + 1) % 4], level, thickness: 200, height: 3 })).id);
  await cmd("model.add_door", { wall: walls[0], offset: 2000, width: 900, height: 2100 });
  await cmd("model.add_window", { wall: walls[2], offset: 3000, width: 1200, height: 1200, sill: 900 });
  await page.reload(); await settle();
  await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
});
after(async () => { await browser?.close(); server?.kill(); if (dataDir) rmSync(dataDir, { recursive: true, force: true }); });

describe("rooms and columns", () => {
  it("draws a room on the plan from clicked corners with Enter, and labels it with its name, number and area", async () => {
    const screen = await planScreen();
    await insertTool("Room");
    for (const p of [[1000, 1000], [3000, 1000], [3000, 3000], [1000, 3000]]) await clickAt(screen(p));
    await page.keyboard.press("Enter"); await settle();
    const [room] = await model("room");
    assert.ok(room, "a room exists");
    assert.equal(room.polygon.length, 4);
    const xs = room.polygon.map((p) => p[0]), ys = room.polygon.map((p) => p[1]);
    const area = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)) / 1e6;
    assert.ok(Math.abs(room.label.area_m2 - area) < 1e-9, "the label's area is the outline's area");
    assert.ok(area > 3 && area < 5, `about 2 m × 2 m (${area})`);
    assert.equal(room.label.number, "101");
    assert.equal(room.label.name, "Room");
    await backToSelect();
  });

  it("selects a room from the Project Browser and shows its figures in Properties", async () => {
    const studio = (await cmd("model.add_room", { level, points: [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], name: "Studio", height: 3 })).id;
    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
    const item = page.getByRole("treeitem", { name: "Studio", exact: true }).first();
    for (const group of ["Families", "Rooms"]) if (!(await item.count())) { const g = page.getByRole("treeitem", { name: group, exact: true }).first(); if ((await g.getAttribute("aria-expanded")) === "false") { await g.click(); await settle(); } }
    await item.click(); await settle();
    const panel = page.locator("aside, .properties, [aria-label='Properties']").filter({ hasText: "Bounding walls" }).first();
    await panel.waitFor();
    const p = await props(studio);
    assert.equal(rowOf(p, "Area").value, "24.000 m²");
    assert.equal(rowOf(p, "Volume").value, "72.000 m³ (area × height)");
    assert.equal(rowOf(p, "Bounding walls").value, "4");
    assert.equal(rowOf(p, "Doors").value, "1");
    assert.equal(rowOf(p, "Windows").value, "1");
    assert.match(await panel.innerText(), /Bounding walls\s*4/);
  });

  it("changes a room's height in Properties and the volume follows; undo puts it back", async () => {
    const [studio] = (await model("room")).filter((r) => r.name === "Studio");
    const field = page.getByRole("spinbutton", { name: /Height/ }).first();
    await field.fill("3.5"); await field.press("Enter"); await settle();
    assert.equal(rowOf(await props(studio.id), "Volume").value, "84.000 m³ (area × height)");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal(rowOf(await props(studio.id), "Volume").value, "72.000 m³ (area × height)");
  });

  it("places a column by clicking the plan, selects it, and it is a real solid of the stated size", async () => {
    await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
    const screen = await planScreen();
    await insertTool("Column");
    await clickAt(screen([4500, 2000]));
    const [col] = await model("column");
    assert.ok(col, "a column is on the plan");
    const xs = col.polygon.map((p) => p[0]), ys = col.polygon.map((p) => p[1]);
    assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - 300) < 1e-6 && Math.abs(Math.max(...ys) - Math.min(...ys) - 300) < 1e-6, "300 mm × 300 mm");
    assert.equal(rowOf(await props(col.id), "Top above level").value, 3.0);
    await backToSelect();
    const studio = (await model("room")).find((r) => r.name === "Studio");
    assert.equal(rowOf(await props(studio.id), "Columns inside").value, "1", "and it is counted in the room it stands in");
  });

  it("places a column in the 3D view from the sizes in the tool bar", async () => {
    await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
    await insertTool("Column");
    await page.getByRole("spinbutton", { name: "Top above level (m)" }).first().fill("4"); 
    const overlay = page.locator(".view-overlay").first();
    const box = await overlay.boundingBox();
    const s = await overlay.evaluate((el, q) => el.spadraView.screenOf(q), [3, 2, 0]);
    await page.mouse.move(box.x + s[0], box.y + s[1], { steps: 3 }); await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
    await backToSelect();
    const cols = await model("column");
    assert.equal(cols.length, 2);
    const made = cols.find((c) => Math.abs((c.polygon[0][0] + c.polygon[2][0]) / 2 - 3000) < 1.5);
    assert.ok(made, "the new column stands where the 3D click landed");
    assert.equal(rowOf(await props(made.id), "Top above level").value, 4.0);
  });

  it("builds a room schedule from the rooms, with totals, and downloads it as CSV", async () => {
    await page.getByRole("tab", { name: "Insert", exact: true }).click();
    await page.getByRole("button", { name: "Schedule", exact: true }).first().click();
    await page.getByRole("menuitem", { name: "Room Schedule" }).click(); await settle();
    const table = page.locator("table[aria-label], table").first();
    await table.waitFor();
    const text = await page.locator(".schedule-scroll, main").first().innerText();
    assert.match(text, /Studio/);
    assert.match(text, /24(\.0+)?/);
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV", exact: true }).first().click()]);
    const csv = readFileSync(await download.path(), "utf8");
    assert.match(csv, /Area \(m²\)/);
    assert.match(csv, /Studio/);
    assert.match(csv, /Total/);
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, [], problems.join("\n")); });
});
