// End-to-end test of editing walls in the plan like a person does: drawing them there, stretching ends, moving, turning, typing a length,
// moving a section line, and turning grid snap off. Real pointer input in a real browser.
//   node --test tests_e2e/wallediting.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-we-"));
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

describe("walls in the plan", () => {
  it("draws a chain of walls in the plan, joined at the corner", async () => {
    await tool("Wall");
    await clickAt([0, 0]); await clickAt([6000, 0]); await clickAt([6000, 4000]);
    await page.keyboard.press("Escape"); await settle();
    const w = await walls();
    assert.equal(w.length, 2, "two walls were drawn");
    assert.ok(near(w[0].b, w[1].a, 1e-6), "the second wall starts where the first ended: the chain snapped to the wall's centre line, not its outline");
    assert.equal(w[0].thickness, 250, "thickness comes from the wall options");
    const meta = (await api("/properties", { method: "POST", body: JSON.stringify({ model: w[0].id }) }));
    assert.ok(meta, "the wall has properties");
    const pieces = (await drawing()).model.filter((m) => m.category === "wall");
    assert.equal(pieces.length, 2);
  });

  it("stretches an end by dragging its grip, and the corner follows", async () => {
    await page.keyboard.press("v");
    const [first] = await walls();
    const mid = [(first.a[0] + first.b[0]) / 2, 0];
    await clickAt(mid);
    const hs = await handles();
    assert.equal(hs.filter((x) => x.shape === "square").length, 2, "a selected wall shows a grip at each end");
    assert.equal(hs.filter((x) => x.shape === "turn").length, 1, "and a handle to turn it");
    const b = box(); void b;
    const end = await screen(first.b);
    const target = await screen([8000, 0]);
    await dragFrom(end, target);
    const after = await walls();
    const stretched = after.find((x) => x.id === first.id);
    assert.ok(near(stretched.a, first.a), "the other end stayed put");
    assert.ok(Math.abs(stretched.b[0] - 8000) < 150 && Math.abs(stretched.b[1]) < 1, `the end went where it was dropped (${stretched.b})`);
    const second = after.find((x) => x.id !== first.id);
    assert.ok(near(second.a, [6000, 0]), "the wall that met it did not move");
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await walls()).find((x) => x.id === first.id).b, first.b), "one Undo puts the end back");
  });

  it("keeps a stretched wall straight with Shift", async () => {
    const [first] = await walls();
    await clickAt([3000, 0]);
    const end = await screen(first.b);
    const target = await screen([7000, 400]);
    await page.mouse.move(end[0], end[1], { steps: 3 }); await page.mouse.down();
    await page.keyboard.down("Shift");
    await page.mouse.move(target[0], target[1], { steps: 6 });
    await page.mouse.up(); await page.keyboard.up("Shift"); await settle();
    const now = (await walls()).find((x) => x.id === first.id);
    assert.ok(Math.abs(now.b[1]) < 1e-6, `with Shift the wall stays horizontal (${now.b})`);
    await page.keyboard.press("Control+z"); await settle();
  });

  it("moves a wall by dragging it, even when it was not selected first", async () => {
    await page.mouse.click((await box()).x + 5, (await box()).y + 5); await settle();      // click empty space: nothing selected
    const [, second] = await walls();
    const from = await screen([6000, 2000]);
    const to = await screen([6000 + 1000, 2000]);
    await dragFrom(from, to);
    const moved = (await walls()).find((x) => x.id === second.id);
    assert.ok(Math.abs(moved.a[0] - 6000 - 1000) < 200 && Math.abs(moved.a[1]) < 1, `the wall moved sideways (${moved.a})`);
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await walls()).find((x) => x.id === second.id).a, second.a), "Undo puts it back");
  });

  it("turns the selection by dragging the round handle", async () => {
    await clickAt([3000, 0]);
    const [first] = await walls();
    const turn = (await handles()).find((x) => x.shape === "turn");
    const b = await box();
    const at = [b.x + turn.at[0], b.y + turn.at[1]];
    const centre = await screen([(first.a[0] + first.b[0]) / 2, 0]);
    // drag the handle a quarter turn round the wall's centre: from straight above to straight to the left
    const r = Math.hypot(at[0] - centre[0], at[1] - centre[1]);
    await dragFrom(at, [centre[0] - r, centre[1]]);
    const now = (await walls()).find((x) => x.id === first.id);
    const angle = Math.atan2(now.b[1] - now.a[1], now.b[0] - now.a[0]) * 180 / Math.PI;
    assert.ok(Math.abs(Math.abs(angle) - 90) < 2, `a horizontal wall turned a quarter turn (${angle.toFixed(1)}°)`);
    assert.ok(Math.abs(Math.hypot(now.b[0] - now.a[0], now.b[1] - now.a[1]) - Math.hypot(first.b[0] - first.a[0], first.b[1] - first.a[1])) < 1e-6, "its length is unchanged");
    await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await walls()).find((x) => x.id === first.id).b, first.b), "Undo turns it back");
  });

  it("sets a wall's length by clicking the dimension beside it", async () => {
    const [first] = await walls();
    await clickAt([3000, 0]);
    const label = await view((v) => v.lengthLabelScreen());
    assert.ok(label, "a selected wall shows its length");
    const b = await box();
    await page.mouse.click(b.x + label[0], b.y + label[1]); await settle();
    const dialog = page.locator("dialog[open] input").first();
    await dialog.fill("4500"); await page.getByRole("button", { name: "Set length" }).click(); await settle();
    const now = (await walls()).find((x) => x.id === first.id);
    assert.ok(near(now.a, first.a), "the start stayed put");
    assert.ok(Math.abs(Math.hypot(now.b[0] - now.a[0], now.b[1] - now.a[1]) - 4500) < 1e-6, "the wall is now 4500 mm long");
  });

  it("rotates the selection about a picked centre with the Rotate tool", async () => {
    await page.keyboard.press("v");
    const before = (await walls()).find((x) => x.a[0] === 0 && x.a[1] === 0);
    await clickAt([2000, 0]);
    await tool("Rotate");
    await clickAt([0, 0]);                 // the centre
    await clickAt([2000, 0]);              // the direction to start from
    await clickAt([0, 2000]);              // and where it should end up: a quarter turn
    await page.keyboard.press("Escape"); await settle();
    const now = (await walls()).find((x) => x.id === before.id);
    assert.ok(near(now.a, [0, 0], 1e-3), "the start sits on the centre of rotation");
    assert.ok(Math.abs(now.b[0]) < 1e-3 && now.b[1] > 4000, `the wall now points up the page (${now.b})`);
  });
});

describe("section lines", () => {
  let id;
  it("moves and stretches a section line with its grips", async () => {
    await page.keyboard.press("Escape"); await page.keyboard.press("v");
    const fit = async () => { await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle(); };
    await fit();
    const [lo, hi] = (await drawing()).bounds;
    const y = lo[1] + (hi[1] - lo[1]) * 0.1, x0 = lo[0] + (hi[0] - lo[0]) * 0.05, x1 = lo[0] + (hi[0] - lo[0]) * 0.8;      // inside the fitted view
    await tool("Section");
    await clickAt([x0, y]); await clickAt([x1, y]);                                    // drawn in the plan, like a person would
    await page.getByRole("tab", { name: /^Section/ }).first().waitFor();
    id = (await api("/project/tree")).tree.sections.flatMap(function flat(n) { return [n, ...(n.children ?? []).flatMap(flat)]; }).find((n) => n.view_kind === "section").id;
    await page.getByRole("tab", { name: /^Level 01/ }).first().click(); await settle();
    await fit();
    await page.keyboard.press("v");
    const marks = () => drawing().then((d) => d.section_marks.find((m) => m.id === id));
    const before = await marks();
    const midX = (before.a[0] + before.b[0]) / 2;
    await clickAt([midX, before.a[1]]);
    const hs = await handles();
    assert.equal(hs.filter((x) => x.shape === "square").length, 2, "a selected section line has a grip at each end");
    const b = await box();
    const end = [b.x + hs[1].at[0], b.y + hs[1].at[1]];
    const goal = [before.b[0] + 600, before.b[1]];
    await dragFrom(end, await screen(goal));
    const stretched = await marks();
    assert.ok(Math.abs(stretched.b[0] - goal[0]) < 150 && Math.abs(stretched.b[1] - goal[1]) < 150, `the end went where it was dropped (${stretched.b})`);
    assert.ok(near(stretched.a, before.a), "the other end stayed put");
    const from = await screen([midX, before.a[1]]);
    await dragFrom(from, await screen([midX, before.a[1] + 800]));
    const moved = await marks();
    const dy = moved.a[1] - before.a[1];
    assert.ok(dy > 400 && dy < 1200, `dragging the line moved it up the page (${dy})`);
    assert.ok(Math.abs((moved.b[1] - stretched.b[1]) - dy) < 1e-6 && Math.abs(moved.a[0] - before.a[0]) < 1e-6 && Math.abs(moved.b[0] - stretched.b[0]) < 1e-6, "both ends moved by the same amount, and only the way it was dragged");
    await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z"); await settle();
    assert.ok(near((await marks()).b, before.b), "Undo restores the line");
  });
});

describe("grid snap", () => {
  it("can be turned off, so the pointer is not pulled to the grid", async () => {
    await page.keyboard.press("Escape"); await settle();
    const toggle = page.locator("button.snap-toggle");
    assert.equal(await toggle.getAttribute("aria-pressed"), "true", "grid snap is on to start with");
    await page.keyboard.press("F9"); await settle();
    assert.equal(await toggle.getAttribute("aria-pressed"), "false", "F9 turns it off");
    const count = (await walls()).length;
    await canvas().focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press("-");                                                          // leave empty space round the building
    await tool("Wall");
    const b = await box();
    const a = [b.x + b.width * 0.12, b.y + b.height * 0.15], c = [a[0] + 123.4, a[1] + 87.6];
    await page.mouse.move(a[0], a[1], { steps: 3 }); await page.mouse.click(a[0], a[1]);
    await page.mouse.move(c[0], c[1], { steps: 3 }); await page.mouse.click(c[0], c[1]); await settle();
    await page.keyboard.press("Escape"); await page.keyboard.press("Escape"); await settle();
    const all = await walls();
    assert.equal(all.length, count + 1);
    const w = all[all.length - 1];
    const off = (v) => Math.abs(v - Math.round(v / 10) * 10) > 1e-3;
    assert.ok(off(w.a[0]) || off(w.a[1]), `the first point is not on a grid line (${w.a})`);
    assert.ok(off(w.b[0]) || off(w.b[1]), `the second point is not on a grid line (${w.b})`);
    await page.keyboard.press("F9"); await settle();
    assert.equal(await toggle.getAttribute("aria-pressed"), "true", "F9 turns it back on");
  });
  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});
