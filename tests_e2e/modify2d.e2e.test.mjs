// End-to-end tests of the 2D modify tools (offset, trim, extend, fillet, mirror, hatch) and grip editing, driven with real pointer input.
//   node --test tests_e2e/modify2d.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-mod-"));
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
  const data = await drawing();
  const box = await page.locator(".view-canvas").first().boundingBox();
  const [lo, hi] = data.bounds;
  const k = Math.min((box.width - 128) / (hi[0] - lo[0]), (box.height - 128) / (hi[1] - lo[1]));
  fitted = ([wx, wy]) => [box.x + box.width / 2 + (wx - (lo[0] + hi[0]) / 2) * k, box.y + box.height / 2 - (wy - (lo[1] + hi[1]) / 2) * k];
}
const at = async (world) => { const [x, y] = fitted(world); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
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

describe("2D modify tools in a browser", () => {
  it("offsets a line by a typed distance to the side that was clicked", async () => {
    await scene(async () => { await cmd("draw.line", { start: [0, 0], end: [10000, 0] }); await cmd("draw.line", { start: [0, 6000], end: [10000, 6000] }); });
    await tool("Offset");
    await at([5000, 0]);
    await dialog().getByLabel("Distance (mm)").fill("1500");
    await dialog().getByRole("button", { name: "Set distance" }).click(); await settle();
    await at([5000, 2500]);
    const lines = await byKind("line");
    assert.equal(lines.length, 3);
    const copy = lines.find((l) => l.geometry.start[1] === 1500);
    assert.ok(copy, `an offset line at y=1500 exists: ${JSON.stringify(lines.map((l) => l.geometry))}`);
    assert.deepEqual([copy.geometry.start[0], copy.geometry.end[0]], [0, 10000]);
  });

  it("offsets a rectangle inwards", async () => {
    await scene(async () => { await cmd("draw.rectangle", { corner: [0, 0], width: 8000, height: 5000 }); });
    await tool("Offset");
    await at([0, 2500]);                                          // left edge
    await dialog().getByLabel("Distance (mm)").fill("400");
    await dialog().getByRole("button", { name: "Set distance" }).click(); await settle();
    await at([4000, 2500]);                                       // inside
    const polys = await byKind("polyline");
    assert.equal(polys.length, 2);
    const inner = polys.find((p) => p.geometry.points.some((q) => q[0] === 400));
    assert.ok(inner, "an inner rectangle exists");
    const xs = inner.geometry.points.map((q) => q[0]), ys = inner.geometry.points.map((q) => q[1]);
    assert.deepEqual([Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)], [400, 7600, 400, 4600]);
  });

  it("trims the part of a line between two crossing lines", async () => {
    await scene(async () => {
      await cmd("draw.line", { start: [0, 0], end: [10000, 0] });
      await cmd("draw.line", { start: [4000, -1500], end: [4000, 1500] });
      await cmd("draw.line", { start: [7000, -1500], end: [7000, 1500] });
    });
    await tool("Trim");
    await at([5500, 0]);
    const lines = await byKind("line");
    assert.equal(lines.length, 4, "the trimmed line became two");
    const horizontals = lines.filter((l) => l.geometry.start[1] === 0 && l.geometry.end[1] === 0).map((l) => [l.geometry.start[0], l.geometry.end[0]].sort((a, b) => a - b));
    assert.deepEqual(horizontals.sort((a, b) => a[0] - b[0]), [[0, 4000], [7000, 10000]]);
  });

  it("extends a line to the boundary beyond it", async () => {
    await scene(async () => {
      await cmd("draw.line", { start: [0, 3000], end: [2000, 3000] });
      await cmd("draw.line", { start: [6000, 1500], end: [6000, 4500] });
    });
    await tool("Extend");
    await at([1900, 3000]);
    const target = (await byKind("line")).find((l) => l.geometry.start[1] === 3000 && l.geometry.end[1] === 3000);
    assert.deepEqual(target.geometry.end, [6000, 3000]);
    assert.deepEqual(target.geometry.start, [0, 3000]);
  });

  it("fillets two lines with a radius and adds a tangent arc", async () => {
    await scene(async () => {
      await cmd("draw.line", { start: [0, 0], end: [6000, 0] });
      await cmd("draw.line", { start: [0, 0], end: [0, 6000] });
    });
    await tool("Fillet");
    await at([4500, 0]); await at([0, 4500]);
    await dialog().getByLabel("Radius (mm)").fill("1000");
    await dialog().getByRole("button", { name: "Fillet" }).click(); await settle();
    const arcs = await byKind("arc");
    assert.equal(arcs.length, 1);
    assert.ok(Math.abs(arcs[0].geometry.radius - 1000) < 1e-6);
    assert.ok(Math.abs(arcs[0].geometry.center[0] - 1000) < 1e-6 && Math.abs(arcs[0].geometry.center[1] - 1000) < 1e-6);
    const starts = (await byKind("line")).map((l) => l.geometry.start.map((v) => Math.round(v))).sort();
    assert.deepEqual(starts, [[0, 1000], [1000, 0]]);
  });

  it("hatches the closed shape that was clicked and draws it", async () => {
    await scene(async () => { await cmd("draw.rectangle", { corner: [0, 0], width: 8000, height: 5000 }); await cmd("draw.circle", { center: [12000, 2500], radius: 2000 }); });
    await tool("Hatch");
    await at([2000, 2000]);
    await dialog().getByLabel("Pattern").selectOption("solid");
    await dialog().getByRole("button", { name: "Hatch" }).click(); await settle();
    await tool("Hatch");
    await at([12000, 2500]);
    await dialog().getByLabel("Pattern").selectOption("lines");
    await dialog().getByLabel("Spacing (mm)").fill("250");
    await dialog().getByRole("button", { name: "Hatch" }).click(); await settle();
    const hatches = await byKind("hatch");
    assert.equal(hatches.length, 2);
    assert.deepEqual(hatches.map((h) => h.geometry.pattern).sort(), ["lines", "solid"]);
    const lined = hatches.find((h) => h.geometry.pattern === "lines");
    assert.ok(lined.strokes.length > 10, "the server computed hatch strokes");
    // The fill is really painted: a pixel inside the solid hatch is not the paper colour, one outside it is.
    const pixel = async ([wx, wy]) => {
      const [sx, sy] = fitted([wx, wy]);
      return page.evaluate(([x, y]) => {
        const c = document.querySelector(".view-canvas"); const r = c.getBoundingClientRect(); const k = c.width / r.width;
        return Array.from(c.getContext("2d").getImageData(Math.round((x - r.left) * k), Math.round((y - r.top) * k), 1, 1).data);
      }, [sx, sy]);
    };
    await page.mouse.move(5, 5); await settle();
    const inside = await pixel([4000, 2500]), outside = await pixel([10000, 500]);
    assert.notDeepEqual(inside, outside, "solid hatch changes pixels");
  });

  it("mirrors the selection across two picked points, keeping the original", async () => {
    await scene(async () => { await cmd("draw.rectangle", { corner: [1000, 1000], width: 3000, height: 2000 }); await cmd("draw.line", { start: [8000, 0], end: [8000, 1] }); });
    await tool("Select");
    await at([1000, 2000]);                                       // the rectangle's left edge
    await tool("Mirror");
    await at([8000, 0]); await at([8000, 2500]);                   // both inside the fitted view
    await dialog().getByRole("button", { name: "Keep originals" }).click(); await settle();
    const polys = await byKind("polyline");
    assert.equal(polys.length, 2);
    const xs = polys.map((p) => Math.min(...p.geometry.points.map((q) => q[0]))).sort((a, b) => a - b);
    assert.deepEqual(xs, [1000, 12000]);                           // 16000 - 4000, mirrored about x = 8000
  });

  it("mirror without a selection explains what to do instead of failing", async () => {
    await scene(async () => { await cmd("draw.line", { start: [0, 0], end: [4000, 0] }); });
    await tool("Mirror");
    await at([1000, 0]);
    await page.getByText("Select what to mirror first").first().waitFor();
    assert.equal((await byKind("line")).length, 1);
  });

  it("drags a grip of the selected line and the edit can be undone", async () => {
    await scene(async () => { await cmd("draw.line", { start: [0, 0], end: [8000, 0] }); await cmd("draw.line", { start: [0, 6000], end: [1, 6000] }); });
    await tool("Select");
    await at([4000, 0]);                                          // select the line
    const [sx, sy] = fitted([8000, 0]), [tx, ty] = fitted([8000, 3000]);
    await page.mouse.move(sx, sy); await page.mouse.down(); await page.mouse.move(tx, ty, { steps: 8 }); await page.mouse.up(); await settle();
    const moved = (await byKind("line")).find((l) => l.geometry.start[0] === 0 && l.geometry.start[1] === 0);
    assert.ok(Math.abs(moved.geometry.end[1] - 3000) < 60, `end moved up: ${JSON.stringify(moved.geometry)}`);
    await page.keyboard.press("Control+z"); await settle();
    const back = (await byKind("line")).find((l) => l.geometry.start[0] === 0 && l.geometry.start[1] === 0);
    assert.deepEqual(back.geometry.end, [8000, 0]);
  });

  it("drags the radius grip of a circle", async () => {
    await scene(async () => { await cmd("draw.circle", { center: [5000, 3000], radius: 1000 }); await cmd("draw.line", { start: [0, 0], end: [1, 0] }); await cmd("draw.line", { start: [10000, 6000], end: [10001, 6000] }); });
    await tool("Select");
    await at([5000, 4000]);                                       // the circle's rim
    const [sx, sy] = fitted([6000, 3000]), [tx, ty] = fitted([7000, 3000]);
    await page.mouse.move(sx, sy); await page.mouse.down(); await page.mouse.move(tx, ty, { steps: 8 }); await page.mouse.up(); await settle();
    const [c] = await byKind("circle");
    assert.ok(Math.abs(c.geometry.radius - 2000) < 80, `radius ${c.geometry.radius}`);
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, []);
  });
});
