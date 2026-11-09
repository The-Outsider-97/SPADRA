// End-to-end test of drawing closed profiles in the plan and using them as a door frame profile.
//   node --test tests_e2e/profiles.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-prof-"));
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
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);
const cmd = async (op, params) => api("/command", { method: "POST", body: JSON.stringify({ op, params: { drawing: await planId(), ...params } }) }).then((r) => r.result);
const byKind = async (kind) => (await drawing()).entities.filter((e) => e.kind === kind);

/** Empty the plan, add geometry through the API, reload the page and fit, so screen positions can be computed from world positions. */
let fitted = null;
async function scene(build) {
  const d = await drawing();
  if (d.entities.length) await cmd("draw.delete", { ids: d.entities.map((e) => e.id) });
  await build();
  await page.reload(); await settle();
  await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
  // Screen positions come from the view's own projection at the moment of use, not from an assumed symmetrical margin around the fit: choosing a ribbon tab
  // changes the ribbon's height and so moves the canvas on the page, and the old fixed formula then put a click 10 px off the line it meant.
  fitted = (world) => page.locator(".view-canvas").first().evaluate((el, [wx, wy]) => {
    const r = el.getBoundingClientRect(), [x, y] = el.spadraView.screenOf([wx, wy]);
    return [r.x + x, r.y + y];
  }, world);
}
const at = async (world) => { const [x, y] = await fitted(world); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
const tool = async (name) => {
  const ribbon = ["Line", "Polyline", "Rectangle", "Circle", "Offset", "Fillet"].includes(name)
    ? "Annotate" : name === "Trim" ? "Modifier" : "Model";
  await page.getByRole("tab", { name: ribbon, exact: true }).click();
  await page.getByRole("button", { name, exact: true }).first().click();
};
const dialog = () => page.getByRole("dialog");

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});


const scene3 = async () => (await api("/scene")).objects;
const canvas = () => page.locator("canvas[aria-label='Drawing canvas']");

describe("closed SDRW profiles for a door frame", () => {
  let doorId;
  it("draws two L-shaped polylines, one closed with C and one by coming back to its first point, and both are closed shapes", async () => {
    const level = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
    const wall = (await cmd("model.add_wall", { start: [0, 0], end: [6000, 0], level, height: 3.2, thickness: 250 })).id;
    doorId = (await cmd("model.add_door", { wall, point: [3000, 0], width: 900, height: 2100 })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
    await page.getByRole("tab", { name: "Annotate", exact: true }).click();
    await page.getByRole("button", { name: "Polyline", exact: true }).first().click();
    // An L, 200 wide (a frame profile may be 5-250 mm across) and 400 tall, inside the part of the plan the fitted view shows.
    const L = (x, y) => [[x, y], [x + 200, y], [x + 200, y + 200], [x + 100, y + 200], [x + 100, y + 400], [x, y + 400]];
    const draw = async (points) => {
      for (const p of points) {
        const [sx, sy] = await canvas().evaluate((el, q) => { const r = el.getBoundingClientRect(), s = el.spadraView.screenOf(q); return [r.x + s[0], r.y + s[1]]; }, p);
        await page.mouse.move(sx, sy, { steps: 2 }); await page.mouse.click(sx, sy); await settle();
      }
    };
    await page.keyboard.press("F9"); await settle();                                                  // grid snap off: the plan's grid is coarser than this shape
    await draw(L(500, 400)); await page.keyboard.press("c"); await settle();                          // closed with C
    await draw([...L(3500, 400), L(3500, 400)[0]]);                                                  // back to its first point: that click closes it
    const shapes = (await api(`/drawing/${(await api("/project/tree")).tree.sections[0].children[1].children[0].id}`)).entities.filter((e) => e.kind === "polyline");
    assert.equal(shapes.length, 2, "two polylines were drawn");
    assert.ok(shapes.every((e) => e.geometry.closed === true && e.geometry.points.length === 6), JSON.stringify(shapes.map((e) => [e.geometry.closed, e.geometry.points.length])));
  });

  it("offers both in Use Existing SDRW Profile, and the chosen one becomes the frame", async () => {
    await page.getByRole("tab", { name: "Model", exact: true }).click(); await page.getByRole("button", { name: "Select", exact: true }).first().click();
    const door = (await (await api("/drawing/" + (await api("/project/tree")).tree.sections[0].children[1].children[0].id)).model).find((m) => m.id === doorId);
    const centre = door.polygon.reduce((a, q) => [a[0] + q[0] / door.polygon.length, a[1] + q[1] / door.polygon.length], [0, 0]);
    const [sx, sy] = await canvas().evaluate((el, q) => { const r = el.getBoundingClientRect(), s = el.spadraView.screenOf(q); return [r.x + s[0], r.y + s[1]]; }, centre);
    await page.mouse.click(sx, sy, { button: "right" }); await settle();
    await page.getByRole("menuitem", { name: /Use Existing SDRW Profile/ }).click();
    await page.getByRole("dialog").waitFor();                       // the chooser reads every drawing of the project first
    const options = page.getByRole("dialog").getByLabel("Closed SDRW profile").locator("option");
    assert.equal(await options.count(), 2, "both closed polylines are offered");
    await page.getByRole("dialog").getByRole("button", { name: "Add profile" }).click(); await settle();
    assert.ok((await scene3()).some((o) => o.category === "opening_profile"), "the frame was made from the profile");
  });

  it("raised no errors in the browser", () => { assert.deepEqual(problems, []); });
});

describe("a dimension on wall faces follows a layer added to the wall", () => {
  it("moves the dimension's point with the face, and one Undo puts both back", async () => {
    const tree = await api("/project/tree");
    const level = tree.levels.find((l) => l.name === "Level 01").id;
    const plan = tree.tree.sections[0].children[1].children[0].id;
    await cmd("model.add_wall", { start: [0, 4000], end: [6000, 4000], level, height: 3.2, thickness: 250 });
    const dim = (await cmd("draw.dimension", { drawing: plan, p1: [4500, 125], p2: [4500, 3875], dimline_point: [5200, 2000] })).id;
    const geometry = async () => (await api(`/drawing/${plan}`)).entities.find((e) => e.id === dim).geometry;
    assert.deepEqual((await geometry()).p1, [4500, 125]);
    await page.reload(); await settle();
    await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
    await page.getByRole("tab", { name: "Model", exact: true }).click(); await page.getByRole("button", { name: "Select", exact: true }).first().click();
    const wall = (await api(`/drawing/${plan}`)).model.find((m) => m.category === "wall" && m.axis.a[1] === 0 && m.axis.b[1] === 0);
    const [sx, sy] = await canvas().evaluate((el, q) => { const r = el.getBoundingClientRect(), s = el.spadraView.screenOf(q); return [r.x + s[0], r.y + s[1]]; }, [1000, 0]);
    await page.mouse.click(sx, sy); await settle();
    await page.getByRole("button", { name: "Add left wall layer" }).click();
    await page.getByRole("dialog").getByLabel(/Layer thickness/).fill("50");
    await page.getByRole("dialog").getByRole("button", { name: "Add layer" }).click(); await settle();
    const after = await geometry();
    assert.ok(Math.abs(after.p1[1] - 150) < 1e-6 && Math.abs(after.p2[1] - 3875) < 1e-6, `the dimension's point on the thickened wall moved with its face (${after.p1} ${after.p2})`);
    await page.keyboard.press("Control+z"); await settle();
    assert.deepEqual((await geometry()).p1, [4500, 125]);
    assert.ok(wall, "the wall was in the plan");
  });
});
