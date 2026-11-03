// End-to-end test of Extrude, Void, Sweep and Array: drawn with the pointer in the plan, a void cuts the floor under it, Make void and the
// Array dialog work from the ribbon. Real pointer input in a real browser.
//   node --test tests_e2e/forms.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-fm-"));
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
const command = (op, params) => api("/command", { method: "POST", body: JSON.stringify({ op, params }) });

const settle = () => page.waitForTimeout(350);
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
const canvas = () => page.locator("canvas[aria-label='Drawing canvas']");
const view = (fn, arg) => canvas().evaluate((el, [f, a]) => new Function("v", "a", `return (${f})(v, a)`)(el.spadraView, a), [fn.toString(), arg]);

/** Screen position of a world point (drawing units) in the plan. */
const screen = async ([x, y]) => {
  const box = await canvas().boundingBox();
  const s = await view((v, p) => v.screenOf(p), [x, y]);
  return [box.x + s[0], box.y + s[1]];
};
const clickAt = async (world) => { const [x, y] = await screen(world); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
const dragFrom = async (from, to) => {
  await page.mouse.move(from[0], from[1], { steps: 3 }); await page.mouse.down();
  await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 }); await page.mouse.move(to[0], to[1], { steps: 4 });
  await page.mouse.up(); await settle();
};
const walls = async () => {
  const seen = new Map();
  for (const m of (await drawing()).model) if (m.category === "wall" && !seen.has(m.id)) seen.set(m.id, m.axis);
  return [...seen].map(([id, axis]) => ({ id, ...axis }));
};
const near = (a, b, tol = 1e-6) => Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol;
const handles = () => view((v) => v.handleScreens());
const box = async () => canvas().boundingBox();

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  if (process.env.SPADRA_DEBUG) page.on("response", async (r) => { if (r.status() >= 400) console.error("HTTP", r.status(), r.request().method(), r.url().split("/api")[1], r.request().postData()?.slice(0, 160), (await r.text()).slice(0, 200)); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});


const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const items = async (category) => (await drawing()).model.filter((m) => m.category === category);
const row = (p, key) => [...p.top, ...p.groups.flatMap((g) => g.rows)].find((r) => r.key === key);
const reload = async () => { await page.reload(); await settle(); await page.getByRole("button", { name: "2D", exact: true }).click(); await settle(); };
const level = async () => (await drawing()).view.level_id;

describe("extrude, void, sweep and array in a browser", () => {
  it("draws an extrusion with the pointer, with the depth and base from the option strip", async () => {
    await tool("Extrude");
    const depth = page.getByLabel("Depth (m)").first();
    await depth.fill("2.5"); await depth.press("Tab");
    await clickAt([8000, 0]); await clickAt([10000, 0]); await clickAt([10000, 2000]); await clickAt([8000, 2000]);
    await page.keyboard.press("Enter"); await settle();
    const [made] = await items("extrusion");
    assert.ok(made, "an extrusion is on the plan");
    const p = await props(made.id);
    assert.equal(row(p, "depth").value, 2.5);
    assert.equal(row(p, "form").value, "Solid");
    assert.equal(row(p, "base").value, 0);
  });

  it("draws a void inside a floor and the floor gets a hole, drawn on the plan; deleting the void closes it", async () => {
    await command("model.add_floor", { level: await level(), points: [[0, 0], [6000, 0], [6000, 5000], [0, 5000]], thickness: 200 });
    await reload();
    await tool("Void");
    await clickAt([1000, 1000]); await clickAt([2000, 1000]); await clickAt([2000, 2000]); await clickAt([1000, 2000]);
    await page.keyboard.press("Enter"); await settle();
    const [v] = await items("void");
    assert.ok(v, "the void is on the plan");
    const [floor] = await items("floor");
    assert.equal(floor.holes?.length, 1, "the floor is drawn with a hole where the void is");
    const p = await props(v.id);
    assert.ok(JSON.stringify(p).includes("Floor"), "Properties says the void cuts the floor");
    await page.keyboard.press("Delete"); await settle();
    assert.equal(((await items("floor"))[0]).holes, undefined, "without the void the floor is whole again");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal(((await items("floor"))[0]).holes?.length, 1, "Undo brings the void and the hole back together");
  });

  it("makes a selected extrusion a void from the ribbon", async () => {
    const [made] = await items("extrusion");
    await page.keyboard.press("v");
    await clickAt([9000, 1000]);
    await page.getByRole("button", { name: "Make void", exact: true }).first().click(); await settle();
    assert.equal((await items("extrusion")).length, 0);
    assert.equal((await items("void")).filter((m) => m.id === made.id).length, 1, "the same element is now a void");
  });

  it("draws a sweep along a path and shows its centre line", async () => {
    await canvas().focus();
    for (let i = 0; i < 3; i++) await page.keyboard.press("-");                    // room on screen for the path
    await tool("Sweep");
    await clickAt([0, -1500]); await clickAt([4000, -1500]); await clickAt([4000, -3500]);              // below the content, clear of the option strip
    await page.keyboard.press("Enter"); await settle();
    const [s] = await items("sweep");
    assert.ok(s?.path?.length === 3, `the sweep's path is on the plan (${JSON.stringify((await drawing()).model.map((m) => [m.category, m.path?.length]))})`);
    const p = await props(s.id);
    assert.equal(row(p, "width").value, 200);
    await command("model.set_property", { id: s.id, key: "width", value: 400 });
    assert.equal(row(await props(s.id), "width").value, 400);
  });

  it("opens the Array dialog for the selection and makes copies", async () => {
    await command("model.add_wall", { level: await level(), start: [0, 12000], end: [3000, 12000], height: 3, thickness: 200 });
    await reload();
    const [wall] = (await items("wall"));
    const before = (await items("wall")).length;
    await page.keyboard.press("v");
    await clickAt([1500, 12000]);
    await page.getByRole("tab", { name: "Insert", exact: true }).click(); await settle();
    await page.getByRole("button", { name: "Array", exact: true }).first().click(); await settle();
    await page.getByLabel("Number of items").fill("3");
    await page.getByLabel(/Step Y/).fill("2000");
    await page.getByLabel(/Step X/).fill("0");
    await page.getByRole("button", { name: "Make copies" }).click(); await settle();
    const after = new Set((await items("wall")).map((m) => m.id));
    assert.ok(wall && after.size === 3 + (before - 1), `two copies were added (${before} -> ${after.size})`);
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
