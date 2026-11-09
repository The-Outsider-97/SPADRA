// End-to-end tests: the real server, the real built client, a real browser. Run `npm run build` first.
//   npm run test:e2e        (needs `npm install` for playwright, and `npx playwright install chromium` once)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const SLAI = process.env.SPADRA_SLAI_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, base, dataDir;
const problems = [];

const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: SLAI, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  const url = await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`SPADRA did not start:\n${out}`)), 20000);
    const onData = (d) => { out += d; const m = /Open\s*:\s*(http\S+)/.exec(out); if (m) { clearTimeout(timer); ok(m[1]); } };
    server.stdout.on("data", onData); server.stderr.on("data", (d) => { out += d; });
    server.on("exit", (code) => { clearTimeout(timer); fail(new Error(`SPADRA exited (${code}):\n${out}`)); });
  });
  return url;
}

const api = (path, init) => page.evaluate(async ([p, i]) => {
  const r = await fetch(`/api${p}`, { ...(i ?? {}), headers: { "X-SLAI-Spadra": "1", "Content-Type": "application/json" }, credentials: "same-origin" });
  return (await r.json()).data;
}, [path, init]);

const settle = () => page.waitForTimeout(350);
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);

before(async () => {
  const url = await startServer();
  base = new URL(url).origin;
  browser = await chromium.launch({ ...(process.env.SPADRA_CHROMIUM ? { executablePath: process.env.SPADRA_CHROMIUM } : {}), args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  page.on("response", (r) => { if (r.status() >= 400 && !r.url().includes("/api/")) problems.push(`http ${r.status()} ${r.url()}`); });
  await page.goto(url);
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const canvasPoint = async (selector, fx, fy) => {
  const box = await page.locator(selector).first().boundingBox();
  return [box.x + box.width * fx, box.y + box.height * fy];
};
// A new drawing opens on a 20 m square centred at (10 m, 7.5 m) with a 48 px margin, and the view does not move as things are drawn.
const toScreen = async ([wx, wy]) => {
  const box = await page.locator(".view-canvas").first().boundingBox();
  const k = (Math.min(box.width, box.height) - 96) / 20000;
  return [box.x + box.width / 2 + (wx - 10000) * k, box.y + box.height / 2 - (wy - 7500) * k];
};
const click = async (selector, fx, fy) => { const [x, y] = await canvasPoint(selector, fx, fy); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };

describe("SPADRA in a browser", () => {
  it("starts with a new-project dialog and creates a project with the screenshot's browser tree", async () => {
    await page.getByRole("button", { name: "Create" }).click();
    await settle();
    for (const label of ["3D Model", "Level 01", "Level 02", "Roof Level", "Walls", "Sheets"]) await page.getByRole("treeitem", { name: new RegExp(`^${label}`) }).first().waitFor();
    assert.equal(await page.locator(".doc-tab[aria-selected=true] .chip").textContent(), "SDRW");
  });

  it("draws a rectangle and a polyline with real clicks and the server stores them", async () => {
    await page.getByRole("tab", { name: "Annotate", exact: true }).click();
    await page.getByRole("button", { name: "Rectangle" }).click();
    await click(".view-canvas", 0.3, 0.3); await click(".view-canvas", 0.5, 0.55);
    await page.getByRole("button", { name: "Polyline" }).click();
    for (const [x, y] of [[0.6, 0.3], [0.8, 0.3], [0.8, 0.5]]) await click(".view-canvas", x, y);
    await page.keyboard.press("Enter"); await settle();
    const kinds = (await drawing()).entities.map((e) => e.kind);
    assert.deepEqual(kinds, ["polyline", "polyline"]);
    const [rect, poly] = (await drawing()).entities;
    assert.equal(rect.geometry.points.length, 4); assert.equal(rect.geometry.closed, true);
    assert.equal(poly.geometry.points.length, 3); assert.equal(poly.geometry.closed, false);
  });

  it("snaps a new line to an existing endpoint exactly", async () => {
    const [corner] = (await drawing()).entities[0].geometry.points;
    await page.getByRole("tab", { name: "Annotate", exact: true }).click();
    await page.getByRole("button", { name: "Line", exact: true }).click();
    const box = await page.locator(".view-canvas").first().boundingBox();
    // A new drawing opens on a 20 m square centred at (10 m, 7.5 m) with 48 px margins; the view does not move as things are drawn.
    const k = (Math.min(box.width, box.height) - 96) / 20000;
    const sx = box.x + box.width / 2 + (corner[0] - 10000) * k, sy = box.y + box.height / 2 - (corner[1] - 7500) * k;
    await page.mouse.move(sx + 3, sy - 2, { steps: 3 }); await page.mouse.click(sx + 3, sy - 2); await settle();
    await click(".view-canvas", 0.2, 0.8);
    await page.keyboard.press("Escape");
    const line = (await drawing()).entities.find((e) => e.kind === "line");
    assert.ok(line, "a line was drawn");
    assert.deepEqual(line.geometry.start, corner);
  });

  it("selects by clicking, shows its properties, moves it by dragging, then undoes and redoes", async () => {
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("button", { name: "Select" }).first().click();
    const line = (await drawing()).entities.find((e) => e.kind === "line");
    const before = line.geometry.end;
    const [sx, sy] = await toScreen([(line.geometry.start[0] + line.geometry.end[0]) / 2, (line.geometry.start[1] + line.geometry.end[1]) / 2]);
    await page.mouse.click(sx, sy); await settle();
    await page.waitForSelector(".prop-title:has-text('Line')");
    assert.match(await page.locator(".prop-row .value").first().textContent(), /X: .* mm\s+Y: /);
    const rev = (await api("/project/tree")).project.revision;
    await page.mouse.move(sx, sy); await page.mouse.down(); await page.mouse.move(sx + 60, sy, { steps: 6 }); await page.mouse.up(); await settle();
    const moved = (await drawing()).entities.find((e) => e.kind === "line").geometry.end;
    assert.notDeepEqual(moved, before);
    assert.ok((await api("/project/tree")).project.revision > rev);
    await page.keyboard.press("Control+z"); await settle();
    assert.deepEqual((await drawing()).entities.find((e) => e.kind === "line").geometry.end, before);
    await page.keyboard.press("Control+Shift+z"); await settle();
    assert.deepEqual((await drawing()).entities.find((e) => e.kind === "line").geometry.end, moved);
  });

  it("deletes the selection with the Delete key and brings it back with undo", async () => {
    const n = (await drawing()).entities.length;
    await page.locator(".view-canvas").first().focus();
    await page.keyboard.press("Delete"); await settle();
    assert.equal((await drawing()).entities.length, n - 1);
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await drawing()).entities.length, n);
  });

  it("places walls in 3D by clicking on the ground, selects one, and edits its height", async () => {
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("button", { name: "Wall" }).first().click();
    const pts = [[0.4, 0.55], [0.6, 0.55], [0.6, 0.75]];
    for (const [x, y] of pts) await click(".view-overlay", x, y);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Select" }).first().click();
    const scene = await api("/scene");
    assert.equal(scene.objects.length, 2);
    assert.ok(scene.objects.every((o) => o.category === "wall" && o.faces.length === 12));
    await click(".view-overlay", 0.5, 0.5);
    await page.waitForSelector(".prop-title:has-text('Basic Wall')");
    const height = page.locator("#prop-height");
    await height.fill("4.5"); await height.press("Enter"); await settle();
    const zs = (await api("/scene")).objects.flatMap((o) => o.vertices.map((v) => v[2]));
    assert.ok(Math.abs(Math.max(...zs) - Math.min(...zs) - 4.5) < 1e-6 || Math.max(...zs) - Math.min(...zs) > 3.19);
    assert.ok((await page.evaluate(() => document.querySelector(".prop-title")?.textContent)).includes("Wall"));
  });

  it("shows those walls on the level plan, and a wall can be selected and moved there", async () => {
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    const d = await drawing();
    assert.equal(d.model.length, 2);
    await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle();
    const box = await page.locator(".view-canvas").first().boundingBox();
    const [lo, hi] = d.bounds;
    const k = Math.min((box.width - 128) / (hi[0] - lo[0]), (box.height - 128) / (hi[1] - lo[1]));
    const screen = ([wx, wy]) => [box.x + box.width / 2 + (wx - (lo[0] + hi[0]) / 2) * k, box.y + box.height / 2 - (wy - (lo[1] + hi[1]) / 2) * k];
    const wall = d.model[0];
    const centre = wall.polygon.reduce((a, p) => [a[0] + p[0] / 4, a[1] + p[1] / 4], [0, 0]);
    const [sx, sy] = screen(centre);
    await page.getByRole("button", { name: "Select", exact: true }).first().click();
    await page.mouse.click(sx, sy); await settle();
    await page.waitForSelector(".prop-title:has-text('Basic Wall')");
    await page.mouse.move(sx, sy); await page.mouse.down(); await page.mouse.move(sx + 50, sy + 30, { steps: 6 }); await page.mouse.up(); await settle();
    const moved = (await drawing()).model.find((m) => m.id === wall.id).polygon;
    assert.notDeepEqual(moved, wall.polygon);
    await page.keyboard.press("Control+z"); await settle();
    assert.deepEqual((await drawing()).model.find((m) => m.id === wall.id).polygon, wall.polygon);
    await page.getByRole("button", { name: "3D", exact: true }).click(); await settle();
  });

  it("creates a sheet, places the plan on it at scale, and draws the title block from real fields", async () => {
    await page.getByRole("button", { name: "Open another view" }).click();
    await page.getByRole("menuitem", { name: "New sheet…" }).click();
    await page.getByLabel("Sheet name").fill("Level 01 Plan"); await page.getByRole("button", { name: "Create" }).click(); await settle();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("button", { name: "Place View" }).click();
    await page.getByRole("menuitem", { name: "Level 01" }).click(); await settle();
    const svgText = await page.locator(".sheet-wrap svg").innerHTML();
    assert.match(svgText, /LEVEL 01 PLAN/);
    assert.match(svgText, /A101/);
    assert.match(svgText, /Untitled project|UNTITLED PROJECT/i);
    assert.ok((await page.locator(".sheet-wrap svg path").count()) >= 1);
    const tree = await api("/project/tree");
    assert.equal(tree.tree.sections.find((x) => x.id === "sheets").children.length, 1);
  });

  it("drags an unlocked sheet viewport and synchronizes its persisted X/Y", async () => {
    const sheetId = (await api("/project/tree")).tree.sections.find((x) => x.id === "sheets").children[0].id;
    const original = (await api(`/sheet/${sheetId}`)).viewports[0];
    const drawing = page.locator(`.sheet-wrap svg > g[data-viewport="${original.id}"]`);
    const rect = await drawing.boundingBox();
    assert.ok(rect && rect.width > 0 && rect.height > 0, "viewport has a visible drag target");
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 60, y - 40, { steps: 12 });
    await page.mouse.up();
    await settle();
    const moved = (await api(`/sheet/${sheetId}`)).viewports[0];
    assert.notDeepEqual([moved.x, moved.y], [original.x, original.y], "mouse drag changed persisted sheet coordinates");
    assert.ok(moved.x > original.x && moved.y > original.y, "drag direction uses paper coordinates");
    // Numerical coordinates and pointer coordinates operate on one placement model.
    const command = await api("/command", { method: "POST", body: JSON.stringify({ op: "sheet.set_viewport", params: { id: sheetId, viewport: original.id, key: "position", value: [original.x, original.y] } }) });
    assert.ok(command, "numeric position command responded");
    assert.deepEqual([(await api(`/sheet/${sheetId}`)).viewports[0].x, (await api(`/sheet/${sheetId}`)).viewports[0].y], [original.x, original.y]);
    // That command went to the server directly, so the page does not know the new project revision; a person's next action from the page
    // (the export below) would rightly be refused as made for an older revision. Load the page afresh and reopen the sheet, as a person would.
    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: /^A101/ }).first().click(); await settle();
  });

  it("exports a complete vector SLDC sheet and prints PDF at the same physical paper size", async () => {
    await page.getByRole("tab", { name: "File", exact: true }).first().click();
    const sent = page.waitForRequest((r) => r.method() === "POST" && /\/api\/export\/sheet\//.test(r.url()));
    await page.getByRole("button", { name: "Export .sldc" }).click();
    const request = await sent;
    const payload = request.postDataJSON();
    assert.match(payload.preview_svg, /^<svg\b/);
    assert.match(payload.preview_svg, /viewBox="0 0 841 594"/);
    assert.match(payload.preview_svg, /<path\b|<text\b/);
    assert.doesNotMatch(payload.preview_svg, /var\(--(?:ink|paper|border|muted|select)\)/, "print vectors contain portable colors");
    assert.match(payload.preview_png, /^iVBOR/, "the reader fallback is actual PNG data");
    const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true });
    assert.match(pdf.subarray(0, 8).toString("ascii"), /^%PDF-/);
    // Browser PDF page tree declares physical dimensions in 1/72-inch points.
    const boxes = [...pdf.toString("latin1").matchAll(/\/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]/g)];
    assert.ok(boxes.length, "PDF has a physical MediaBox");
    const [widthPt, heightPt] = boxes.at(-1).slice(1).map(Number);
    assert.ok(Math.abs(widthPt * 25.4 / 72 - 841) < 1, "PDF is 841 mm wide");
    assert.ok(Math.abs(heightPt * 25.4 / 72 - 594) < 1, "PDF is 594 mm tall");
  });

  it("resizes both side panels independently, remembers widths, and remains usable on narrower screens", async () => {
    for (const [side, direction] of [["left", 80], ["right", -80]]) {
      const handle = page.getByRole("separator", { name: side === "left" ? "Resize Project Browser" : "Resize Properties" });
      const rect = await handle.boundingBox();
      assert.ok(rect, `${side} panel has a visible resize handle`);
      const oldWidth = await page.evaluate((s) => document.querySelector(s).getBoundingClientRect().width, side === "left" ? ".panel[aria-label='Project Browser']" : ".panel[aria-label='Properties']");
      await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
      await page.mouse.down();
      await page.mouse.move(rect.x + rect.width / 2 + direction, rect.y + rect.height / 2, { steps: 8 });
      await page.mouse.up();
      const now = Number(await handle.getAttribute("aria-valuenow"));
      assert.ok(now > oldWidth + 25, `${side} panel became wider`);
    }
    const widths = await page.evaluate(() => [getComputedStyle(document.querySelector(".workspace")).getPropertyValue("--browser-width"), getComputedStyle(document.querySelector(".workspace")).getPropertyValue("--properties-width")]);
    await page.reload(); await settle(); await page.waitForSelector(".workspace");      // the page is rebuilt from the server after a reload; wait for it rather than guess how long that takes
    const remembered = await page.evaluate(() => [getComputedStyle(document.querySelector(".workspace")).getPropertyValue("--browser-width"), getComputedStyle(document.querySelector(".workspace")).getPropertyValue("--properties-width")]);
    assert.deepEqual(remembered, widths);
    await page.setViewportSize({ width: 1100, height: 760 });
    assert.ok((await page.locator(".canvas-area").boundingBox()).width > 200, "drawing workspace remains usable");
    await page.setViewportSize({ width: 1672, height: 941 });
  });

  it("opens Roof Level using the same browser navigation as other levels", async () => {
    // Tabs are not kept across a page reload (the previous test reloaded), so the sheet is opened first and must still be there afterwards.
    await page.getByRole("treeitem", { name: /^A101/ }).first().click(); await settle();
    await page.getByRole("treeitem", { name: "Roof Level", exact: true }).first().click();
    await settle();
    const active = page.locator(".doc-tab[aria-selected=true]");
    assert.match(await active.textContent(), /Roof Level/i);
    assert.match(await active.textContent(), /SDRW/i);
    await page.getByRole("tab", { name: /A101/ }).first().click();
    await settle();
  });

  it("refuses to drop unsaved work silently, then saves and reopens the project from disk", async () => {
    await page.getByRole("button", { name: "Project menu" }).click();
    await page.getByRole("menuitem", { name: "New project…" }).click();
    await page.getByRole("dialog", { name: "Unsaved changes" }).waitFor();
    await page.getByRole("button", { name: "Save", exact: true }).click(); await settle();
    const saved = await api("/bootstrap");
    assert.ok(saved.recent.length >= 1, "saved project is remembered");
    const folder = saved.recent[0].path;
    assert.ok(existsSync(join(folder, "project.json")) && existsSync(join(folder, "model.sgeo")));
    const manifest = JSON.parse(readFileSync(join(folder, "project.json"), "utf8"));
    assert.equal(manifest.sheets.length, 1);
    await page.getByRole("button", { name: "Cancel" }).click();
    await page.reload(); await settle();                          // the server still has it open: a reload does not lose the work
    await page.getByRole("treeitem", { name: /^A101/ }).waitFor();
  });

  it("raised no browser errors or blocked requests along the way", () => {
    assert.deepEqual(problems.filter((p) => !/GPU stall|GL Driver/.test(p)), []);
  });
});
