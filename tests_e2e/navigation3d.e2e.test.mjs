// End-to-end test of moving about the 3D model: orbiting about the selected element, zooming towards the pointer, walking and looking around from
// the keyboard and mouse. Real pointer input in a real browser.
//   node --test tests_e2e/navigation3d.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-nav-"));
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
const overlay = () => page.locator(".view-overlay").first();
const view = (fn, arg) => overlay().evaluate((el, [f, a]) => new Function("v", "a", `return (${f})(v, a)`)(el.spadraView, a), [fn.toString(), arg]);
const box = () => overlay().boundingBox();
const at = async (p) => { const b = await box(); const s = await view((v, q) => v.screenOf(q), p); return [b.x + s[0], b.y + s[1]]; };
const cam = () => view((v) => ({ yaw: v.cam.yaw, pitch: v.cam.pitch, distance: v.cam.distance, target: [...v.cam.target] }));
const drag = async (from, to) => {
  await page.mouse.move(from[0], from[1], { steps: 3 }); await page.mouse.down();
  await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 }); await page.mouse.move(to[0], to[1], { steps: 4 });
  await page.mouse.up(); await settle();
};
const near = (a, b, tol) => Math.hypot(a[0] - b[0], a[1] - b[1]) <= tol;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const level = (await api("/project/tree")).levels[0].id;
  for (const [a, b] of [[[0, 0], [8000, 0]], [[8000, 0], [8000, 6000]], [[8000, 6000], [0, 6000]], [[0, 6000], [0, 0]]]) await command("model.add_wall", { start: a, end: b, level, height: 3, thickness: 200 });
  await page.reload(); await settle();
  await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const EAST = [8, 3, 1.5];                    // the middle of the east wall, in metres

describe("moving about the 3D model", () => {
  it("orbits about the selected element: it stays where it is on the screen while everything else turns round it", async () => {
    await page.mouse.click(...(await at(EAST))); await settle();
    const before = await at(EAST), farBefore = await at([0, 0, 0]);
    const b = await box();
    await drag([b.x + b.width * 0.2, b.y + b.height * 0.85], [b.x + b.width * 0.2 + 180, b.y + b.height * 0.85]);
    assert.ok(near(await at(EAST), before, 1.5), "the selected wall has not moved on the screen");
    assert.ok(!near(await at([0, 0, 0]), farBefore, 20), "the rest of the model has turned round it");
  });

  it("orbits about the middle of the view instead when that is turned off", async () => {
    await page.getByRole("button", { name: /^Orbit around the selected element/ }).click();
    assert.equal(await page.getByRole("button", { name: /^Orbit around the selected element/ }).getAttribute("aria-pressed"), "false");
    const before = await at(EAST);
    const b = await box();
    await drag([b.x + 200, b.y + 200], [b.x + 400, b.y + 200]);
    assert.ok(!near(await at(EAST), before, 20), "now the wall swings away as the view turns about its own middle");
    await page.getByRole("button", { name: /^Orbit around the selected element/ }).click();
  });

  it("zooms towards the pointer: what is under it stays under it", async () => {
    const p = [0, 0, 0];
    const here = await at(p);
    await page.mouse.move(here[0], here[1]);
    const d0 = (await cam()).distance;
    await page.mouse.wheel(0, -400); await settle();
    assert.ok((await cam()).distance < d0 * 0.7, "it zoomed in");
    assert.ok(near(await at(p), here, 1.5), "the corner under the pointer did not slide away");
    await page.mouse.wheel(0, 400); await settle();
  });

  it("walks and orbits from the keyboard", async () => {
    await overlay().focus();
    const c0 = await cam();
    await page.keyboard.press("Control+ArrowUp"); await settle();
    const c1 = await cam();
    assert.ok(Math.hypot(c1.target[0] - c0.target[0], c1.target[1] - c0.target[1]) > 0.3 && Math.abs(c1.target[2] - c0.target[2]) < 1e-9, "Ctrl+Up walks forward along the ground");
    await page.keyboard.press("PageUp"); await settle();
    assert.ok((await cam()).target[2] > c1.target[2] + 0.3, "Page Up raises the camera");
    const yaw = (await cam()).yaw;
    await page.keyboard.press("ArrowLeft"); await settle();
    assert.ok(Math.abs((await cam()).yaw - yaw) > 0.05, "an arrow key orbits");
  });

  it("looks around from where the camera is with Ctrl+drag, without moving the camera", async () => {
    const eyeOf = (c) => [c.target[0] + c.distance * Math.cos(c.pitch) * Math.sin(c.yaw), c.target[1] - c.distance * Math.cos(c.pitch) * Math.cos(c.yaw), c.target[2] + c.distance * Math.sin(c.pitch)];
    const before = await cam();
    const b = await box();
    await page.keyboard.down("Control");
    await drag([b.x + 400, b.y + 300], [b.x + 520, b.y + 330]);
    await page.keyboard.up("Control");
    const after = await cam();
    const e0 = eyeOf(before), e1 = eyeOf(after);
    assert.ok(Math.hypot(e0[0] - e1[0], e0[1] - e1[1], e0[2] - e1[2]) < 1e-6, "the eye stayed put");
    assert.ok(Math.abs(after.yaw - before.yaw) > 0.1, "but the view turned");
  });

  it("snaps to the 0.1 m grid when drawing a wall, and not when grid snap is off", async () => {
    const planId = (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
    const axes = async () => { const seen = new Map(); for (const m of (await api(`/drawing/${planId}`)).model) if (m.category === "wall" && m.axis) seen.set(m.id, m.axis); return [...seen.values()]; };
    const count = (await axes()).length;
    const b = await box();
    const click = async (fx, fy) => { const x = b.x + b.width * fx, y = b.y + b.height * fy; await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
    const offGrid = (v) => Math.abs(v - Math.round(v / 100) * 100) > 1;
    await page.getByRole("button", { name: "Wall", exact: true }).first().click();
    await click(0.31, 0.62); await click(0.43, 0.71); await page.keyboard.press("Escape"); await settle();
    let walls = await axes();
    assert.equal(walls.length, count + 1);
    let w = walls[walls.length - 1];
    assert.ok(![...w.a, ...w.b].some(offGrid), `with grid snap on every end is on a 0.1 m line (${w.a} ${w.b})`);
    await page.keyboard.press("F9"); await settle();
    await page.getByRole("button", { name: "Wall", exact: true }).first().click();
    await click(0.33, 0.52); await click(0.47, 0.57); await page.keyboard.press("Escape"); await settle();
    walls = await axes();
    assert.equal(walls.length, count + 2);
    w = walls[walls.length - 1];
    assert.ok([...w.a, ...w.b].some(offGrid), `with grid snap off the ends fall where the pointer was (${w.a} ${w.b})`);
    await page.keyboard.press("F9"); await settle();
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
