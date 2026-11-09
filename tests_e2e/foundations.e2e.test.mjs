// End-to-end tests of foundations (the Foundation dialog, following the wall and column they stand on, a layered slab, the schedule and the section), in a real browser against the real server.
//   node --test tests_e2e/foundations.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const NESTED_SLAI = resolve(HERE, "..", "..", "..");
const SLAI = existsSync(join(NESTED_SLAI, "applications")) ? NESTED_SLAI : resolve(HERE, "..", "..");
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
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
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
const rowsOf = async (id) => { const p = await props(id); return { top: p.top, groups: p.groups, all: p.groups.flatMap((g) => g.rows).concat(p.top) }; };
const volumeOf = (o) => { let v = 0; for (const f of o.faces) { const [a, b, c] = f.map((i) => o.vertices[i]); v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6; } return Math.abs(v); };


const bbox = (o) => ({ lo: [0, 1, 2].map((i) => Math.min(...o.vertices.map((v) => v[i]))), hi: [0, 1, 2].map((i) => Math.max(...o.vertices.map((v) => v[i]))) });
const select = async (name) => {
  await page.getByLabel("Search the project").fill(name); await settle();
  await page.getByRole("treeitem", { name, exact: true }).first().click(); await settle();
  await page.getByLabel("Search the project").fill(""); await settle();
};

describe("foundations in a browser", () => {
  let level, wallId, columnId, stripId;
  it("Foundation with a wall selected makes a strip under it that is as wide as the wall plus the projection, and Properties says what it stands on", async () => {
    level = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
    wallId = (await cmd("model.add_wall", { level, start: [0, 0], end: [6000, 0], height: 3, thickness: 200 })).id;
    columnId = (await cmd("model.add_column", { level, point: [0, 4000], base: 0, top: 3, width: 300, depth: 300 })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await select("Basic Wall");
    await tool("Foundation");
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Kind").selectOption("strook"); await settle();
    await dialog.getByLabel("Height (mm)").fill("500");
    await dialog.getByRole("button", { name: "Add foundation" }).click(); await settle();
    const strips = await ofCategory("foundation");
    assert.equal(strips.length, 1);
    stripId = strips[0].id;
    const b = bbox(strips[0]);
    assert.ok(Math.abs((b.hi[0] - b.lo[0]) - 6) < 1e-6 && Math.abs((b.hi[1] - b.lo[1]) - 0.6) < 1e-6, "6 m long and 600 mm wide (200 mm wall + 2 x 200 mm)");
    assert.ok(Math.abs(b.hi[2]) < 1e-9 && Math.abs(b.lo[2] + 0.5) < 1e-9, "its top meets the wall's base at the level, 500 mm deep");
    await page.locator(".group-head", { hasText: "Constraints" }).first().waitFor();
    await page.getByText("Declared, not designed or verified").waitFor();
    await page.getByText("Stands on", { exact: true }).first().waitFor();
  });

  it("the strip follows the wall when the wall is stretched, with no other command", async () => {
    await cmd("model.set_wall_ends", { id: wallId, end: [9000, 0] }); await settle();
    const b = bbox((await ofCategory("foundation"))[0]);
    assert.ok(Math.abs((b.hi[0] - b.lo[0]) - 9) < 1e-6, "now 9 m long");
  });

  it("a pad under the column is made from the dialog, and follows the column when it is moved", async () => {
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await select("Column");
    await tool("Foundation");
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Kind").selectOption("poer"); await settle();
    await dialog.getByLabel("Height (mm)").fill("600");
    await dialog.getByLabel("Concrete class").fill("C25/30");
    await dialog.getByRole("button", { name: "Add foundation" }).click(); await settle();
    const pad = (await ofCategory("foundation")).find((o) => o.name === "Pad foundation");
    assert.ok(pad, "a pad exists");
    await cmd("model.move", { id: columnId, dx: 1, dy: 0, dz: 0 }); await settle();
    const moved = (await ofCategory("foundation")).find((o) => o.name === "Pad foundation");
    const a = bbox(pad), c = bbox(moved);
    assert.ok(Math.abs((c.lo[0] - a.lo[0]) - 1) < 1e-6, "the pad moved 1 m with its column");
  });

  it("an insulated ground-bearing slab is built from the dialog with layer rows and declared bars, and its volume is the build-up's", async () => {
    await page.reload(); await settle();                                                                 // the page must know about the change made through the API
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await tool("Foundation");
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Kind").selectOption("vloer"); await settle();
    await dialog.getByLabel("Thickness (mm)", { exact: true }).fill("150");
    await dialog.getByRole("group", { name: /Layers below the slab/ }).getByRole("button", { name: "Add" }).click();
    await dialog.getByLabel("Thickness (mm)", { exact: true }).nth(1).fill("100");
    await dialog.getByLabel("λ (W/m·K)").fill("0.036");
    await dialog.getByRole("group", { name: /Reinforcement/ }).getByRole("button", { name: "Add" }).click();
    await dialog.getByRole("button", { name: "Add foundation" }).click(); await settle();
    const slab = (await ofCategory("foundation")).find((o) => o.name === "Ground-bearing slab");
    assert.ok(slab, "the slab exists");
    assert.ok(Math.abs(volumeOf(slab) - 30 * 0.25) < 1e-6, "6 x 5 m, 150 mm of concrete and 100 mm of insulation");
    await page.getByText(/Thermal resistance of the build-up/).waitFor();
    await page.getByText(/no conductivity for Concrete/).waitFor();                                  // honest: the concrete's conductivity was not given
    const quantities = await api("/foundation/report", { method: "POST", body: JSON.stringify({ id: slab.id }) });
    assert.ok(Math.abs(quantities.concrete_m3 - 4.5) < 1e-9);
    assert.equal(quantities.thermal.complete, false);
    assert.ok(quantities.reinforcement.bars > 0);
  });

  it("a section through the slab hatches each layer as its own material, and the Foundation Schedule lists the foundations", async () => {
    const view = await cmd("view.add_section", { a: [0, 2500], b: [6000, 2500] });
    const data = await api(`/drawing/${view.id}`);
    const slab = data.projection.items.find((i) => i.category === "foundation" && i.cut_material?.includes("EPS"));
    assert.ok(slab, "the slab's cut is tagged with its layers");
    assert.deepEqual([...slab.cut_material].sort(), ["EPS", "Reinforced concrete"]);                  // the dialog declared bars, so the concrete is shown as reinforced
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await page.getByRole("button", { name: "Schedule", exact: true }).first().click();
    await page.getByRole("menuitem", { name: "Foundation Schedule" }).click(); await settle();
    const table = page.getByRole("table").first();
    await table.waitFor();
    const text = await table.innerText();
    assert.match(text, /FD01/);
    assert.match(text, /Strip foundation/);
    assert.match(text, /Pad foundation|Pad/);
    assert.match(text, /Ground-bearing slab/);
  });

  it("lets a person select either the wall or its foundation at their coincident section boundary", async () => {
    const section = await cmd("view.add_section", { a: [4500, -1000], b: [4500, 1000], name: "Wall foundation selection" });
    const proj = (await api(`/drawing/${section.id}`)).projection;
    const wall = proj.items.find((item) => item.category === "wall" && item.id === wallId);
    const strip = proj.items.find((item) => item.category === "foundation" && item.id === stripId);
    assert.ok(wall?.cut.length && strip, "the wall and strip foundation both intersect this section");
    const wc = wall.cut.flat(), fc = strip.cut.flat();
    const wMin = Math.min(...wc.map((p) => p[0])), wMax = Math.max(...wc.map((p) => p[0]));
    const fMin = Math.min(...fc.map((p) => p[0])), fMax = Math.max(...fc.map((p) => p[0]));
    const world = [(Math.max(wMin, fMin) + Math.min(wMax, fMax)) / 2, 0];

    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: /Wall foundation selection/ }).first().click(); await settle();
    const c = page.locator("canvas[aria-label='Drawing canvas']");
    const hits = await c.evaluate((el, at) => el.spadraView.modelCandidates(at), world);
    assert.ok(hits.some((hit) => hit.id === wallId) && hits.some((hit) => hit.category === "foundation"), "both objects are in the hit stack");
    const screen = await c.evaluate((el, at) => el.spadraView.screenOf(at), world), box = await c.boundingBox();
    const x = box.x + screen[0], y = box.y + screen[1];
    await page.mouse.click(x, y); await settle();
    let selected = await c.evaluate((el) => el.spadraView.store.selection?.ids?.[0]);
    assert.equal(selected, wallId, "the wall wins the initial ambiguous pick");
    // The stack at this spot is the wall plus every foundation whose cut covers it (earlier tests leave a slab here too),
    // so repeated clicks walk the whole stack; the strip must be reached within one full pass and the wall must come back after it.
    const seen = [selected];
    for (let i = 1; i < hits.length && selected !== strip.id; i += 1) {
      await page.mouse.click(x, y); await settle();
      selected = await c.evaluate((el) => el.spadraView.store.selection?.ids?.[0]);
      seen.push(selected);
    }
    assert.equal(selected, strip.id, `clicking again cycles to the foundation (visited ${seen.join(", ")})`);
    assert.equal(new Set(seen).size, seen.length, "each click moves on to a different element");
  });
});

describe("foundations under several connected walls", () => {
  const X = 30000, Y = 30000, W = 6000, H = 4000;                                                     // far from the earlier tests' elements
  let lvl, walls, floorId, ids;
  const selectModel = (sel) => overlay().evaluate((el, v) => el.spadraView.store.select({ kind: "model", ids: v }), sel);
  const floorBox = async () => bbox((await ofCategory("floor")).find((o) => o.id === floorId));

  it("a closed ring of four selected walls gets a strip under each in one dialog, as the screenshot's error asked for, and the floor over them is cut back", async () => {
    lvl = (await api("/project/tree")).levels.find((l) => l.name === "Level 01").id;
    const seg = [[[X, Y], [X + W, Y]], [[X + W, Y], [X + W, Y + H]], [[X + W, Y + H], [X, Y + H]], [[X, Y + H], [X, Y]]];
    walls = [];
    for (const [a, b] of seg) walls.push((await cmd("model.add_wall", { level: lvl, start: a, end: b, height: 3, thickness: 200 })).id);
    floorId = (await cmd("model.add_floor", { level: lvl, points: [[X, Y], [X + W, Y], [X + W, Y + H], [X, Y + H]], thickness: 200 })).id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await selectModel(walls);
    await tool("Foundation");
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Kind").selectOption("strook"); await settle();
    await dialog.getByLabel("Height (mm)").fill("500");
    assert.equal(await dialog.getByLabel("Constrained to the wall or column above").isChecked(), true, "constrained by default");
    await dialog.getByLabel("Add formwork").check();
    await dialog.getByLabel("Formwork thickness (mm)").fill("50");
    await dialog.getByRole("button", { name: "Add foundation" }).click(); await settle();
    await page.getByRole("dialog").waitFor({ state: "detached" }); await settle();                  // the dialog closes once the server has built all four
    assert.equal(await page.getByRole("dialog").count(), 0, "the dialog closed: no error about a single wall");
    const strips = (await ofCategory("foundation")).filter((o) => bbox(o).lo[0] > X / 1000 - 1);
    assert.equal(strips.length, 4, "a strip under each wall");
    const fb = await floorBox();
    assert.ok(Math.abs(fb.lo[0] - (X / 1000 + 0.3)) < 1e-6 && Math.abs(fb.hi[0] - ((X + W) / 1000 - 0.3)) < 1e-6, "the floor was cut back to the strips' inner edges (600 mm wide strips)");
  });

  it("moving a wall moves its strip, and moving it down lowers the strip", async () => {
    const before = (await ofCategory("foundation")).filter((o) => bbox(o).lo[0] > X / 1000 - 1).map(bbox);
    await cmd("model.move", { id: walls[0], dx: 0, dy: 0, dz: -0.3 }); await settle();
    const after = (await ofCategory("foundation")).filter((o) => bbox(o).lo[0] > X / 1000 - 1).map(bbox);
    const lowered = after.filter((b, i) => Math.abs(b.hi[2] - before[i].hi[2] + 0.3) < 1e-6);
    assert.ok(lowered.length >= 1, "at least the south strip went down with its wall");
    await cmd("model.move", { id: walls[0], dx: 0, dy: 0, dz: 0.3 }); await settle();
  });

  it("Reinforce… after creation declares bars, and the foundation is then shown and hatched as reinforced concrete", async () => {
    await page.reload(); await settle();                                                                 // the page must know about the moves made through the API
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    const strip = (await ofCategory("foundation")).find((o) => bbox(o).lo[0] > X / 1000 - 1);
    const colourBefore = strip.color;
    await selectModel([strip.id]); await settle();
    await page.getByRole("button", { name: "Reinforce…" }).click(); await settle();                     // the Properties panel's own link
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Concrete class").fill("C30/37");
    await dialog.getByRole("button", { name: "Apply" }).click(); await settle();
    const reinforced = (await ofCategory("foundation")).find((o) => o.id === strip.id);
    assert.notEqual(reinforced.color, colourBefore, "the 3D colour changed with the declaration");
    const view = await cmd("view.add_section", { a: [X + 3000, Y - 1000], b: [X + 3000, Y + 1000], name: "Reinforced strip" });
    const proj = (await api(`/drawing/${view.id}`)).projection;
    const item = proj.items.find((i) => i.id === strip.id);
    assert.ok(item?.cut_material?.includes("Reinforced concrete"), "the section hatches it as reinforced concrete");
    await page.reload(); await settle();                                                                 // the section was added through the API
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await selectModel([strip.id]); await settle();
    await page.getByRole("button", { name: "Reinforce…" }).click(); await settle();
    await page.getByRole("dialog").getByRole("button", { name: "Remove reinforcement" }).click(); await settle();
    assert.equal((await ofCategory("foundation")).find((o) => o.id === strip.id).color, colourBefore, "removing the bars restores the shading");
  });
});

describe("the page after the several-walls tests", () => {
  it("the page logged no errors", () => { assert.deepEqual(problems, []); });
});
