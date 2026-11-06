// End-to-end test of editable grid lines in a real browser against the real server: select a grid in the plan, stretch an end with its grip, type its length
// in the box beside it, set Angle / Label / Bubbles in Properties, and see the same grid (same label) in a section. Real pointer input.
//   node --test tests_e2e/grids.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-gr-"));
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
const cmd = async (op, params) => (await api("/command", { method: "POST", body: JSON.stringify({ op, params }) })).result;

const settle = () => page.waitForTimeout(350);
const tree = async () => (await api("/project/tree")).tree;
const flat = (n) => [n, ...(n.children ?? []).flatMap(flat)];
const planId = async () => (await tree()).sections[0].children[1].children[0].id;
const drawing = async (id) => api(`/drawing/${id ?? await planId()}`);
const canvas = () => page.locator("canvas[aria-label='Drawing canvas']");
const view = (fn, arg) => canvas().evaluate((el, [f, a]) => new Function("v", "a", `return (${f})(v, a)`)(el.spadraView, a), [fn.toString(), arg]);
const handles = () => view((v) => v.handleScreens());
const box = () => canvas().boundingBox();
const near = (a, b, tol = 1e-6) => Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol;
const fit = async () => { await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle(); };

const screen = async ([x, y]) => {
  const b = await box();
  const s = await view((v, p) => v.screenOf(p), [x, y]);
  return [b.x + s[0], b.y + s[1]];
};
const clickAt = async (world) => { const [x, y] = await screen(world); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
const dragFrom = async (from, to) => {
  await page.mouse.move(from[0], from[1], { steps: 3 }); await page.mouse.down();
  await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 }); await page.mouse.move(to[0], to[1], { steps: 4 });
  await page.mouse.up(); await settle();
};
/** The grids in the plan, once each, with their axis in drawing units and their label. */
const grids = async () => {
  const seen = new Map();
  for (const m of (await drawing()).model) if (m.category === "grid" && !seen.has(m.id)) seen.set(m.id, m);
  return [...seen.values()].map((m) => ({ id: m.id, a: m.axis.a, b: m.axis.b, label: m.label.name, bubbles: m.label.bubbles }));
};
const reload = async () => {
  await page.reload(); await settle();
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
  await page.getByRole("tab", { name: /^Level 01/ }).first().click(); await settle();
  await fit();
};

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  if (process.env.SPADRA_DEBUG) page.on("response", async (r) => { if (r.status() >= 400) console.error("HTTP", r.status(), r.request().method(), r.url().split("/api")[1], r.request().postData()?.slice(0, 160), (await r.text()).slice(0, 200)); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const level = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
  await cmd("model.add_wall", { level, start: [0, 0], end: [8000, 0] });                        // something for the plan to fit around
  await cmd("model.add_wall", { level, start: [8000, 0], end: [8000, 5000] });
  await cmd("model.add_grid", { start: [3000, -1500], end: [3000, 6000], label: "B" });
  await cmd("model.add_grid", { start: [-1000, 2500], end: [9000, 2500] });
  await reload();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("grid lines in the plan", () => {
  let gid;
  it("selecting a grid shows a grip at each end and the length beside it", async () => {
    await page.keyboard.press("v");
    gid = (await grids()).find((g) => g.label === "B").id;
    await clickAt([3000, 4000]);
    const hs = await handles();
    assert.equal(hs.filter((x) => x.shape === "square").length, 2, "a selected grid has a grip at each end");
    assert.ok(await view((v) => v.lengthLabelScreen()), "and its length is shown");
  });

  it("stretches an end by dragging its grip, and one Undo puts it back", async () => {
    const before = (await grids()).find((g) => g.id === gid);
    const end = await screen(before.b);
    await dragFrom(end, await screen([3000, 7000]));
    const now = (await grids()).find((g) => g.id === gid);
    assert.ok(near(now.a, before.a), "the other end stayed put");
    assert.ok(Math.abs(now.b[0] - 3000) < 150 && Math.abs(now.b[1] - 7000) < 150, `the end went where it was dropped (${now.b})`);
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await grids()).find((g) => g.id === gid).b, before.b), "Undo restores the end");
  });

  it("sets the length by clicking the dimension beside it", async () => {
    await clickAt([3000, 4000]);
    const label = await view((v) => v.lengthLabelScreen());
    const b = await box();
    await page.mouse.click(b.x + label[0], b.y + label[1]); await settle();
    await page.locator("dialog[open] input").first().fill("6000");
    await page.getByRole("button", { name: "Set length" }).click(); await settle();
    const now = (await grids()).find((g) => g.id === gid);
    assert.ok(near(now.a, [3000, -1500]), "the start stayed put");
    assert.ok(Math.abs(Math.hypot(now.b[0] - now.a[0], now.b[1] - now.a[1]) - 6000) < 1e-6, "the grid is 6000 mm long");
  });
});

let refused = 0;
describe("grid lines in Properties", () => {
  let gid;
  it("shows Start, End, Length, Angle and Bubbles, and changes the angle with the start held", async () => {
    await page.keyboard.press("v");
    gid = (await grids()).find((g) => g.label === "B").id;
    await clickAt([3000, 3000]);
    const angle = page.getByLabel("Angle", { exact: true });
    await angle.waitFor();
    assert.equal(await angle.inputValue(), "90");
    await angle.fill("45"); await angle.press("Enter"); await settle();
    const g = (await grids()).find((x) => x.id === gid);
    assert.ok(near(g.a, [3000, -1500], 1e-6), "the start held");
    assert.ok(Math.abs(Math.atan2(g.b[1] - g.a[1], g.b[0] - g.a[0]) * 180 / Math.PI - 45) < 1e-6, "the grid now runs at 45 degrees");
    assert.ok(Math.abs(Math.hypot(g.b[0] - g.a[0], g.b[1] - g.a[1]) - 6000) < 1e-6, "and its length is unchanged");
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await grids()).find((x) => x.id === gid).b, [3000, 4500]), "Undo turns it back");
  });

  it("refuses a duplicate label and says so, and accepts a new one", async () => {
    await clickAt([3000, 3000]);
    const other = (await grids()).find((g) => g.id !== gid);
    const name = page.getByLabel("Label", { exact: true });
    await name.fill(String(other.label)); await name.press("Enter"); await settle();
    assert.equal((await grids()).find((g) => g.id === gid).label, "B", "the label was not changed");
    refused = problems.filter((m) => /409/.test(m)).length;
    assert.equal(refused, 1, "the server refused it (a Conflict), not silently");
    assert.ok(await page.locator(".toast[role=alert]").filter({ hasText: /grid labelled|already exists/i }).count() > 0, "and the person is told, in a notification");
    await clickAt([3000, 3000]);
    await page.getByLabel("Label", { exact: true }).fill("C"); await page.getByLabel("Label", { exact: true }).press("Enter"); await settle();
    assert.equal((await grids()).find((g) => g.id === gid).label, "C", "the label changed");
  });

  it("puts bubbles at both ends when asked", async () => {
    await clickAt([3000, 3000]);
    await page.getByLabel("Bubbles", { exact: true }).selectOption("both"); await settle();
    assert.deepEqual((await grids()).find((g) => g.id === gid).bubbles, ["start", "end"]);
  });
});

describe("the same grid in a section", () => {
  it("a section that crosses the grid shows its mark with the label it has now", async () => {
    await cmd("view.add_section", { a: [500, 1000], b: [5500, 1000], name: "Cross" });
    await page.reload(); await settle();
    const id = (await tree()).sections.flatMap(flat).find((n) => n.view_kind === "section" && n.label === "Cross")?.id;
    assert.ok(id, "the section exists");
    const d = await drawing(id);
    const marks = d.projection.grid_marks;
    assert.ok(marks.some((m) => m.name === "C"), `the section carries grid C (${JSON.stringify(marks)})`);
  });

  it("renaming the grid reaches the section", async () => {
    const id = (await tree()).sections.flatMap(flat).find((n) => n.view_kind === "section" && n.label === "Cross").id;
    const gridId = (await grids()).find((g) => g.label === "C")?.id;
    assert.ok(gridId, "the grid is in the plan");
    await cmd("model.set_grid", { id: gridId, label: "D" });
    const d = await drawing(id);
    const marks = d.projection.grid_marks;
    assert.ok(marks.some((m) => m.name === "D") && !marks.some((m) => m.name === "C"), `the section follows (${JSON.stringify(marks)})`);
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems.filter((m) => !/409/.test(m)), [], "no errors apart from the one refused duplicate label"); assert.equal(problems.length, refused); });
});
