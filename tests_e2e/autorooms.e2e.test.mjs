// End-to-end test of automatic rooms: draw walls in the plan until they close a space and a room appears, edit a wall and the room follows,
// open the space and it goes, turn the setting off. Real pointer input in a real browser.
//   node --test tests_e2e/autorooms.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-ar-"));
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


const rooms = async () => (await drawing()).model.filter((m) => m.category === "room");
const area = (poly) => Math.abs(poly.reduce((s, p, k) => s + p[0] * poly[(k + 1) % poly.length][1] - poly[(k + 1) % poly.length][0] * p[1], 0)) / 2;

describe("automatic rooms in a browser", () => {
  it("makes no room until the last wall closes the space, then makes one, drawn on the plan with its name, number and area", async () => {
    await tool("Wall");
    await clickAt([0, 0]); await clickAt([6000, 0]); await clickAt([6000, 4000]); await clickAt([0, 4000]);
    assert.equal((await rooms()).length, 0, "three walls enclose nothing");
    await clickAt([0, 0]);
    await page.keyboard.press("Escape"); await settle();
    const made = await rooms();
    assert.equal(made.length, 1, "the fourth wall closed the space");
    assert.ok(Math.abs(area(made[0].polygon) - (6000 - 250) * (4000 - 250)) < 1, `the room runs between the inner faces of the 250 mm walls (${area(made[0].polygon)})`);
    assert.equal(made[0].label.number, "101");
    assert.equal(made[0].label.name, "Room");
    assert.ok((await api("/project/tree")).tree, "the project still loads");
  });

  it("keeps the room, with the name or number the person gave it, when a wall is stretched past the corner", async () => {
    const [room] = await rooms();
    await command("model.set_property", { id: room.id, key: "number", value: "K-1" });
    await page.reload(); await settle();                                          // the client learns of a change made outside it by loading again
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    const before = area((await rooms())[0].polygon);
    await page.keyboard.press("v");
    await clickAt([3000, 0]);                                                      // select the south wall
    const squares = (await view((v) => v.handleScreens())).filter((x) => x.shape === "square");
    assert.equal(squares.length, 2);
    const b = await canvas().boundingBox();
    const end = squares.map((q) => [b.x + q.at[0], b.y + q.at[1]]).sort((p, q) => q[0] - p[0])[0];      // its east end
    await dragFrom(end, await screen([7000, 0]));
    const reshaped = await rooms();
    assert.equal(reshaped.length, 1, "still one room");
    assert.equal(reshaped[0].id, room.id, "the same room");
    assert.equal(reshaped[0].label.number, "K-1", "the number the person gave it is kept");
    assert.ok(Math.abs(area(reshaped[0].polygon) - before) < 1, "a wall running past the corner encloses nothing more, so the room is as it was");
  });

  it("keeps a room the person made their own when its space is opened, says it is not enclosed, and gives it back its space when the walls close", async () => {
    await page.keyboard.press("Escape");
    await clickAt([0, 2000]);                                                       // the west wall
    await page.keyboard.press("Delete"); await settle();
    assert.equal((await rooms()).length, 1, "the room with a number of its own is kept");
    const warnings = (await api("/check")).issues ?? (await api("/check")).problems ?? [];
    assert.ok(JSON.stringify(warnings).includes("no longer enclosed"), "the model check says the room is not enclosed");
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await rooms()).length, 1, "Undo brings the wall and the room back together");
    assert.ok(!JSON.stringify((await api("/check")).issues ?? (await api("/check")).problems ?? []).includes("no longer enclosed"));
  });

  it("can be turned off from the ribbon, and then a newly closed space gets no room", async () => {
    await page.getByRole("tab", { name: "Insert", exact: true }).click(); await settle();
    await page.getByRole("button", { name: "Auto rooms: on", exact: true }).click(); await settle();
    assert.ok(await page.getByRole("button", { name: "Auto rooms: off", exact: true }).isVisible(), "the button now says it is off");
    assert.equal((await api("/project/tree")).project.auto_rooms, false);
    await canvas().focus();
    for (let i = 0; i < 3; i++) await page.keyboard.press("-");                    // room on screen for a second building
    await tool("Wall");
    const area2 = await canvas().boundingBox();
    const at = (fx, fy) => [area2.x + area2.width * fx, area2.y + area2.height * fy];
    const [p1, p2, p3, p4] = [at(0.72, 0.25), at(0.92, 0.25), at(0.92, 0.55), at(0.72, 0.55)];
    for (const q of [p1, p2, p3, p4, p1]) { await page.mouse.move(q[0], q[1], { steps: 3 }); await page.mouse.click(q[0], q[1]); await settle(); }
    await page.keyboard.press("Escape"); await settle();
    assert.equal((await rooms()).length, 1, "no room was made for the second box");
    await page.getByRole("button", { name: "Auto rooms: off", exact: true }).click(); await settle();
    assert.equal((await rooms()).length, 2, "turning it on makes the room for the space that was already closed");
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
