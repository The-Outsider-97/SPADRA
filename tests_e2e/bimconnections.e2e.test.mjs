// End-to-end test of the issue-sheet interactions (11-11-2026) in a real browser against the real server: a run of connected curtain walls drawn in the plan
// and finished with Enter, double-click to pick one panel for Properties, the corner chosen from the context menu, a drip edge added from a roof's menu,
// a downpipe component added from its menu, a ground drain placed from a floor's menu, and a door leaf dragged open in 3D. Real pointer input and the real API.
//   node --test tests_e2e/bimconnections.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-bim-"));
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

const cmd = async (op, params) => (await api("/command", { method: "POST", body: JSON.stringify({ op, params }) })).result;
const partProps = (id, part) => api("/properties", { method: "POST", body: JSON.stringify({ model: id, part }) });
const canvas2d = () => page.locator(".view-canvas").first();
const plan = async (p) => { const box = await canvas2d().boundingBox(); const s = await canvas2d().evaluate((el, q) => el.spadraView.screenOf(q), p); return [box.x + s[0], box.y + s[1]]; };
const menuItem = (name) => page.getByRole("menuitem", { name, exact: true });
const openPlan = async (name) => {
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
  await page.getByRole("treeitem", { name: new RegExp(`^${name}`) }).first().dblclick(); await settle();
  await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
};
const select3d = async (nameOrId) => {
  await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
  const obj = (await scene()).find((o) => o.id === nameOrId);
  const name = obj ? obj.name : nameOrId;
  await page.getByLabel("Search the project").fill(name); await settle();
  await page.getByRole("treeitem", { name: new RegExp(`^${name}(?!s)`) }).first().click(); await settle();          // the element, not its family ("Downpipes")
  await page.getByLabel("Search the project").fill(""); await settle();
};
let l1, roofLevel, run;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/status of 400/.test(m.text())) problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const levels = (await api("/project/tree")).levels;
  l1 = levels[0].id; roofLevel = levels.find((l) => l.name === "Roof Level").id;
  await cmd("model.add_floor", { points: [[-1000, -1000], [9000, -1000], [9000, 7000], [-1000, 7000]], level: l1 });
  await page.reload(); await settle(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
  assert.deepEqual(problems, [], "the browser reported no errors");
});

describe("issue-sheet interactions in a browser", () => {
  it("draws connected curtain walls in plan: clicks for corners, Enter to finish, then one dialog", async () => {
    await openPlan("Level 01");
    await page.getByRole("tab", { name: "Insert", exact: true }).click();
    await page.getByRole("button", { name: "Connected curtain walls", exact: true }).first().click(); await settle();
    for (const p of [[0, 0], [6000, 0], [6000, 4000]]) { const s = await plan(p); await page.mouse.move(s[0], s[1], { steps: 3 }); await page.mouse.click(s[0], s[1]); await settle(); }
    await page.keyboard.press("Enter");
    const dlg = page.getByRole("dialog", { name: "Curtain wall" });
    await dlg.waitFor();
    await dlg.getByRole("button", { name: "Add 2 curtain walls" }).click(); await settle(); await settle();
    const walls = await ofCategory("curtainwall");
    assert.equal(walls.length, 2, "one curtain wall per segment");
    run = walls.map((w) => w.id);
    const ends = await Promise.all(run.map((id) => props(id)));
    assert.ok(ends.every((p) => JSON.stringify(p).includes("Ends and attachment")), "each knows its ends");
    assert.match(JSON.stringify(ends), /Quad|Trapezoid|L-Corner|V-Corner/, "the right-angle junction got a corner");
  });

  it("double-clicking a curtain wall in plan selects the panel under the pointer for Properties", async () => {
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("button", { name: "Select", exact: true }).first().click(); await settle();
    const s = await plan([1000, 0]);
    await page.mouse.dblclick(s[0], s[1]); await settle(); await settle();
    await page.waitForSelector(".prop-title:has-text('Panel')", { timeout: 5000 });
  });

  it("changes the corner from the curtain wall's context menu, and the server checks it", async () => {
    const s = await plan([3000, 0]);
    await page.mouse.click(s[0], s[1], { button: "right" }); await settle();
    await menuItem("Corner at End…").click();
    const dlg = page.getByRole("dialog", { name: "Curtain wall corner" });
    await dlg.waitFor();
    await dlg.getByLabel("Corner", { exact: true }).selectOption("trapezoid");
    await dlg.getByRole("button", { name: "Apply" }).click(); await settle();
    const all = JSON.stringify(await Promise.all(run.map((id) => props(id))));
    assert.match(all, /Trapezoid/);
  });

  it("offers the curtain-wall corner on the plan, at the corner, like a wall corner", async () => {
    const s = await plan([3000, 0]);
    await page.mouse.click(s[0], s[1]); await settle();
    await page.getByRole("button", { name: "Edit curtain wall corner at end" }).click();
    await page.getByRole("group", { name: "Curtain wall corner type" }).getByRole("button", { name: "V-Corner" }).click(); await settle(); await settle();
    const all = JSON.stringify(await Promise.all(run.map((id) => props(id))));
    assert.match(all, /V-Corner/);
  });

  it("adds a drip edge from the roof's context menu and from a roof edge member's menu", async () => {
    await cmd("model.add_roof", { points: [[0, 6000], [8000, 6000], [8000, 11000], [0, 11000]], level: roofLevel, roof: "gable", pitch: 30, thickness: 200 });
    await page.reload(); await settle(); await settle();
    await select3d("Gable Roof");
    await overlay().focus();
    await page.keyboard.press("Shift+F10");
    await menuItem("Add Drip Edge").click();
    const prompt = page.getByRole("dialog", { name: "Add drip edge" });
    await prompt.waitFor();
    await prompt.getByRole("textbox").fill("1");
    await prompt.getByRole("button", { name: "Add drip edge" }).click(); await settle(); await settle();
    assert.equal((await ofCategory("flashing")).length, 1, "a drip edge on edge 1");
    const roof = (await ofCategory("roof"))[0].id;
    const member = (await cmd("model.add_roof_accessory", { roof, kind: "roof_edge", edge: "3" })).id;
    await page.reload(); await settle(); await settle();
    await select3d("Roof Edge 3");
    await overlay().focus();
    await page.keyboard.press("Shift+F10");
    await menuItem("Add Drip Edge on This Edge").click(); await settle(); await settle();
    const drips = await ofCategory("flashing");
    assert.equal(drips.length, 2);
    assert.ok(member);
  });

  it("adds a downpipe component from its context menu", async () => {
    const roof = (await ofCategory("roof"))[0].id;
    const gutter = (await cmd("model.add_gutters", { roof, edges: [0], downpipe: false })).gutters[0];
    const pipe = (await cmd("model.add_downpipe", { gutter, offset: 4.0 })).id;
    await page.reload(); await settle(); await settle();
    await select3d(pipe);
    await overlay().focus();
    await page.keyboard.press("Shift+F10");
    await menuItem("Add Offset bend").click(); await settle(); await settle();
    const p = await props(pipe);
    assert.deepEqual(p.assembly.map((c) => c.kind), ["pipe", "offset_bend"]);
    const runs = (await scene()).find((o) => o.id === pipe).components;
    assert.ok(runs.some((r) => r.part === "comp:c2"), "the new fitting is its own pickable part");
    assert.ok((await partProps(pipe, "comp:c2")).top.length > 0);
  });

  it("places a ground drain from a floor's context menu, which cuts the floor", async () => {
    await openPlan("Level 01");
    const floor = (await ofCategory("floor"))[0];
    const before = floor.vertices.length;
    const s = await plan([2000, 5500]);
    await page.mouse.click(s[0], s[1], { button: "right" }); await settle();
    await menuItem("Add Point Drain Here…").click();
    const dlg = page.getByRole("dialog", { name: "Add a ground drain" });
    await dlg.waitFor();
    await dlg.getByRole("button", { name: "Add drain" }).click(); await settle(); await settle();
    assert.equal((await ofCategory("drain")).length, 1);
    const after = (await ofCategory("floor"))[0];
    assert.notEqual(after.vertices.length, before, "the floor has the drain's opening");
  });

  it("runs the connection check and proposes a correction for review in the assistant", async () => {
    await page.getByRole("tab", { name: "Analyze", exact: true }).click();
    await page.getByRole("button", { name: "Connections", exact: true }).first().click();
    const dlg = page.getByRole("dialog", { name: "Connection check" });
    await dlg.waitFor();
    await dlg.getByLabel("What to check").selectOption("model");
    await dlg.locator("[role='status']").filter({ hasText: /the model at revision \d+/ }).waitFor({ timeout: 10000 });
    const box = dlg.getByRole("checkbox", { name: /Include: Add a ground inlet/ }).first();
    if (await box.count()) await box.check(); else await dlg.getByRole("checkbox", { name: /^Include:/ }).first().check();
    const before = (await api("/project/tree")).project?.revision;
    await dlg.getByRole("button", { name: "Propose selected corrections" }).click(); await settle();
    await page.getByRole("complementary", { name: "SLAI Assistant" }).waitFor();
    const hist = await api("/assistant");
    const last = hist.messages[hist.messages.length - 1];
    assert.equal(last.kind, "proposal");
    assert.equal(last.proposal.status, "pending", "nothing applied until approved");
    assert.equal((await api("/project/tree")).project?.revision, before);
    await page.getByRole("tab", { name: "Model", exact: true }).click();
  });

  it("explains a spacing that cannot be built, says where, and applies the correction picked", async () => {
    const id = run[0];
    await cmd("model.set_property", { id, key: "h_mode", value: "count" });
    await page.reload(); await settle(); await settle();
    await select3d(id);
    const field = page.getByRole("region", { name: "Properties" }).getByLabel("Horizontal members (transoms): number of members", { exact: true });
    await field.fill("60"); await field.press("Enter"); await settle();
    const dlg = page.getByRole("dialog", { name: "This change cannot be built as it is" });
    await dlg.waitFor();
    assert.match(await dlg.textContent(), /bottom member \(25 mm above the base\) and transom 1/, "it names the two members and where they are");
    await dlg.getByRole("button", { name: /^Instead: Use \d+ interior transoms/ }).click(); await settle(); await settle();
    const rows = (await props(id)).groups.flatMap((g) => g.rows).concat((await props(id)).top);
    const n = rows.find((r) => r.key === "h_value").value;
    assert.ok(n > 1 && n < 60, `a count that fits was applied (${n})`);
  });

  it("explains a roof coming down into a door field, says which field and by how much, and offers corrections", async () => {
    const wall = (await cmd("model.add_curtainwall", { start: [4000, 6300], end: [4000, 10700], level: l1, height: 3000, v_mode: "count", v_value: 2, h_mode: "count", h_value: 0 })).id;
    await cmd("model.set_property", { id: wall, key: "panel:0:0:type", value: "Door" });
    await cmd("model.set_property", { id: wall, key: "name", value: "Gable end glazing" });
    await page.reload(); await settle(); await settle();
    await select3d(wall);
    await overlay().focus();
    await page.keyboard.press("Shift+F10");
    await menuItem("Attach Top to Roof…").click(); await settle(); await settle();
    const dlg = page.getByRole("dialog", { name: "This change cannot be built as it is" });
    await dlg.waitFor();
    const text = await dlg.textContent();
    assert.match(text, /door in field 1\.1 \(1st field from the start, 1st from the base\)/, "it names the field");
    assert.match(text, /cut off by [\d,]+ mm/, "it says by how much");
    assert.doesNotMatch(text, /whole field/);
    assert.ok(await dlg.getByRole("button", { name: /^Make field 1\.1 a window instead of a door, then make the change$/ }).count(), "a correction that leaves the roof attached");
    await dlg.getByRole("button", { name: /^Make field 1\.1 a window instead of a door/ }).click(); await settle(); await settle();
    let attached = "";
    for (let i = 0; i < 20 && !/Attached to roof/.test(attached); i++) { attached = JSON.stringify(await props(wall)); if (!/Attached to roof/.test(attached)) await page.waitForTimeout(300); }
    assert.match(attached, /Attached to roof/, "after the correction the top follows the roof");
    assert.match(attached, /"value":"Window"/, "and field 1.1 is now a window");
  });

  it("drags a door leaf open in 3D and the opening angle is stored", async () => {
    const wall = (await cmd("model.add_wall", { start: [0, 3000], end: [5000, 3000], level: l1, thickness: 200, height: 3 })).id;
    const door = (await cmd("model.add_door", { wall, offset: 2.0, width: 900 })).id;
    await page.reload(); await settle(); await settle();
    await select3d(door);
    const grip = page.getByRole("button", { name: "Open or close this door leaf" });
    await grip.waitFor({ timeout: 5000 });
    const h = (await scene()).find((o) => o.id === door).door_handles[0];
    const box = await grip.boundingBox();
    const hinge = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), h.hinge);
    const ob = await overlay().boundingBox();
    // Drag the grip a third of the way round about the hinge, on screen, the way the leaf turns (back to the grip's side of the wall if that is the other way)
    const sx = box.x + box.width / 2, sy = box.y + box.height / 2, hx = ob.x + hinge[0], hy = ob.y + hinge[1];
    const tryAngle = async (sign) => {
      const a = sign * Math.PI / 3, vx = sx - hx, vy = sy - hy;
      const tx = hx + Math.cos(a) * vx - Math.sin(a) * vy, ty = hy + Math.sin(a) * vx + Math.cos(a) * vy;
      const g = await page.getByRole("button", { name: "Open or close this door leaf" }).boundingBox();
      await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2); await page.mouse.down();
      await page.mouse.move((sx + tx) / 2, (sy + ty) / 2, { steps: 4 }); await page.mouse.move(tx, ty, { steps: 4 });
      await page.mouse.up(); await settle(); await settle();
      return (await scene()).find((o) => o.id === door).door_handles[0];
    };
    let shown = await tryAngle(1);
    if (shown.angle < 20) shown = await tryAngle(-1);
    assert.ok(shown.open, "the leaf is now shown open");
    assert.ok(shown.angle >= 20 && shown.angle <= 170, `the dragged opening angle is stored (${shown.angle})`);
    const rows = (await props(door)).top.concat((await props(door)).groups.flatMap((g) => g.rows));
    assert.ok(rows.some((r) => r.key === "opening_angle" && Math.abs(Number(r.value) - shown.angle) < 1e-6), "Properties shows the same angle");
  });
});
