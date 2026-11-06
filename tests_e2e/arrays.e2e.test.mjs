// End-to-end test of persistent arrays in a real browser against the real server: make one from the Array dialog, change its count and spacing in
// Properties, see the source reach the copies, edit a copy by hand, release the array, undo. Real pointer input.
//   node --test tests_e2e/arrays.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-ay-"));
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
const reload = async () => {
  await page.reload(); await settle();
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
  await page.getByRole("tab", { name: /^Level 01/ }).first().click(); await settle();
  await fit();
};
const walls = async () => {
  const seen = new Map();
  for (const m of (await drawing()).model) if (m.category === "wall" && !seen.has(m.id)) seen.set(m.id, m.axis);
  return [...seen].map(([id, axis]) => ({ id, ...axis })).sort((a, b) => a.a[1] - b.a[1]);
};
const doors = async () => new Set((await drawing()).model.filter((m) => m.category === "door").map((m) => m.id)).size;
const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const rows = (p) => [...p.top, ...p.groups.flatMap((g) => g.rows)];
const arrayId = async () => {
  const cats = (await tree()).sections.find((n) => n.id === "categories").children.find((c) => c.label === "Arrays");
  return cats?.children[0]?.id;
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
  const wall = (await cmd("model.add_wall", { level, start: [0, 0], end: [3000, 0], height: 3, thickness: 200 })).id;
  await cmd("model.add_door", { wall, offset: 1.5 });
  await reload();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const select = async (word, name) => {
  await page.getByLabel("Search the project").fill(word); await settle();
  await page.getByRole("treeitem", { name }).first().click(); await settle();
  await page.getByLabel("Search the project").fill(""); await settle();
};

describe("a persistent array in a browser", () => {
  it("makes an array of a wall and its door from the Array dialog", async () => {
    await page.keyboard.press("v");
    await clickAt([400, 0]);                                        // the wall, not its door
    await page.getByRole("tab", { name: "Insert", exact: true }).click(); await settle();
    await page.getByRole("button", { name: "Array", exact: true }).first().click(); await settle();
    await page.getByLabel("Directions").selectOption("Y");
    await page.getByLabel("Y: number of items").fill("3");
    await page.getByLabel(/Y: spacing/).fill("4000");
    await page.getByRole("button", { name: "Make array" }).click(); await settle();
    const w = await walls();
    assert.deepEqual(w.map((x) => x.a[1]), [0, 4000, 8000], "three walls, 4 m apart");
    assert.equal(await doors(), 3, "the door travelled with each wall");
    assert.ok(await arrayId(), "the array is an element of its own, listed under Arrays");
  });

  it("changes the count and the spacing in Properties, and the existing copies stay the same elements", async () => {
    const before = await walls();
    await select("Array", /^Array 3/);
    const count = page.getByLabel("Count 1", { exact: true });
    await count.fill("4"); await count.press("Enter"); await settle();
    let w = await walls();
    assert.equal(w.length, 4);
    assert.ok(before.every((b) => w.some((x) => x.id === b.id)), "the three that were there are untouched");
    const spacing = page.getByLabel("Spacing 1", { exact: true });
    await spacing.fill("5000"); await spacing.press("Enter"); await settle();
    w = await walls();
    assert.deepEqual(w.map((x) => x.a[1]), [0, 5000, 10000, 15000]);
    assert.ok(before.every((b) => w.some((x) => x.id === b.id)), "moved to the new spacing, not made again");
    assert.equal(await doors(), 4);
  });

  it("a change to the source reaches every copy, and Undo takes it all back in one step", async () => {
    const source = (await walls())[0];
    await cmd("model.set_property", { id: source.id, key: "height", value: 4 });
    const heights = [];
    for (const w of await walls()) heights.push(rows(await props(w.id)).find((r) => r.key === "height").value);
    assert.deepEqual(heights, [4, 4, 4, 4]);
    await reload();
    await page.keyboard.press("Control+z"); await settle();
    const back = [];
    for (const w of await walls()) back.push(rows(await props(w.id)).find((r) => r.key === "height").value);
    assert.deepEqual(back, [3, 3, 3, 3], "one Undo: the source and its four copies");
  });

  it("a copy changed by hand becomes independent and the array leaves it alone", async () => {
    const [, second] = await walls();
    await cmd("model.set_property", { id: second.id, key: "height", value: 6 });
    const arr = await arrayId();
    const items = (await props(arr)).groups.find((g) => g.title === "Items").rows;
    assert.equal(items.find((r) => r.label === "Independent").value, "1");
    const source = (await walls())[0];
    await cmd("model.set_property", { id: source.id, key: "height", value: 5 });
    const h = [];
    for (const w of await walls()) h.push(rows(await props(w.id)).find((r) => r.key === "height").value);
    assert.deepEqual(h, [5, 6, 5, 5], "the independent copy kept its height");
    const p = await props(second.id);
    assert.ok(!p.groups.some((g) => g.title === "Array"), "and it no longer says it is part of the array");
  });

  it("Release array keeps every wall and drops the pattern; Undo brings the pattern back", async () => {
    await reload();
    const [first, , third] = await walls();
    await clickAt([400, third.a[1]]);
    await page.getByRole("tab", { name: "Insert", exact: true }).click(); await settle();
    await page.getByRole("button", { name: "Release array", exact: true }).first().click(); await settle();
    assert.equal(await arrayId(), undefined);
    assert.equal((await walls()).length, 4);
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(await arrayId(), "undone");
    void first;
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
