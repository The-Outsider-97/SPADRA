// End-to-end tests of sections, elevations and schedules in a real browser against the real server.
//   node --test tests_e2e/documentation.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-doc-"));
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
const settle = () => page.waitForTimeout(400);
const flat = (nodes) => nodes.flatMap((n) => [n, ...flat(n.children ?? [])]);
const tree = async () => flat((await api("/project/tree")).tree.sections);
const viewId = async (label) => (await tree()).find((n) => n.label === label && n.open)?.id;
const scene = async () => (await api("/scene")).objects;
const openFromTree = async (label) => { await page.getByRole("treeitem", { name: label, exact: true }).first().click(); await settle(); };
const tab = (name) => page.getByRole("tab", { name: new RegExp(`^${name}`) });
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
const canvas = () => page.locator(".view-canvas").first();
const screen2d = (p) => canvas().evaluate((el, q) => el.spadraView.screenOf(q), p);
const fit = async () => { await page.getByRole("button", { name: "Zoom to fit", exact: true }).first().click(); await settle(); };
const clickWorld = async (p, opts = {}) => {
  const box = await canvas().boundingBox(), [x, y] = await screen2d(p);
  await page.mouse.move(box.x + x, box.y + y, { steps: 3 });
  if (opts.double) await page.mouse.dblclick(box.x + x, box.y + y); else await page.mouse.click(box.x + x, box.y + y);
  await settle();
};
/** The colour of one pixel of the 2D canvas at a world point. */
const pixel = (p) => canvas().evaluate((el, q) => {
  const [x, y] = el.spadraView.screenOf(q), dpr = window.devicePixelRatio || 1;
  const d = el.getContext("2d").getImageData(Math.round(x * dpr), Math.round(y * dpr), 1, 1).data;
  return [d[0], d[1], d[2]];
}, p);
const luma = ([r, g, b]) => (r + g + b) / 3;
/** The darkest pixel (as brightness) in a square of ``r`` pixels either side of a world point. */
const darkestAround = (p, r = 12) => canvas().evaluate((el, [q, r]) => {
  const [x, y] = el.spadraView.screenOf(q), dpr = window.devicePixelRatio || 1;
  const px = Math.max(0, Math.round(x * dpr) - r), py = Math.max(0, Math.round(y * dpr) - r);
  const d = el.getContext("2d").getImageData(px, py, 2 * r + 1, 2 * r + 1).data;
  let darkest = 255;
  for (let i = 0; i < d.length; i += 4) darkest = Math.min(darkest, ((d[i] ?? 255) + (d[i + 1] ?? 255) + (d[i + 2] ?? 255)) / 3);
  return darkest;
}, [p, r]);

let walls = [], ids = {};

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const levels = (await api("/project/tree")).levels, l1 = levels[0].id;
  const room = [[0, 0], [8000, 0], [8000, 5000], [0, 5000]];
  for (let i = 0; i < 4; i++) walls.push((await cmd("model.add_wall", { start: room[i], end: room[(i + 1) % 4], level: l1, thickness: 250, height: 3.2 })).id);
  ids.door = (await cmd("model.add_door", { wall: walls[0], point: [2000, 0], width: 900, height: 2100 })).id;
  ids.window = (await cmd("model.add_window", { wall: walls[0], point: [5500, 0], width: 1800, height: 1300, sill: 900 })).id;
  ids.door2 = (await cmd("model.add_door", { wall: walls[2], point: [4000, 5000], width: 1000, height: 2100 })).id;
  await cmd("model.add_floor", { points: room, level: l1, thickness: 200 });
  await page.reload(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("sections, elevations and schedules in a browser", () => {
  it("lists the four elevations in the Project Browser and shows the model in one", async () => {
    const labels = (await tree()).filter((n) => n.view_kind === "elevation").map((n) => n.label);
    assert.deepEqual(labels, ["East", "North", "South", "West"]);
    await openFromTree("South");
    assert.ok(await tab("South").isVisible());
    const data = await api(`/drawing/${await viewId("South")}`);
    assert.equal(data.projection.kind, "elevation");
    const front = data.projection.items.find((i) => i.id === walls[0]);
    assert.ok(front && front.tris.length > 2, "the south wall's face, with its openings, is there");
    // the picture on the canvas: a wall face is paper-coloured with a dark outline, the sky around it is the paper too, but the outline is dark
    await fit();
    const [[x0, y0], [x1, y1]] = data.projection.bounds;
    const dark = await pixel([x0, (y0 + y1) / 2]);                        // the left edge of the building: its outline
    assert.ok(luma(dark) < 140 || luma(await pixel([x0 - 40, (y0 + y1) / 2])) > 200, "an outline at the building's edge");
    assert.equal((await api("/check")).ok, true);
  });

  it("selects the element under the pointer in an elevation, and shows its properties", async () => {
    await tool("Select");
    const data = await api(`/drawing/${await viewId("South")}`);
    const win = data.projection.items.find((i) => i.id === ids.window);
    const xs = win.tris.flatMap((t) => [t[0], t[2], t[4]]), zs = win.tris.flatMap((t) => [t[1], t[3], t[5]]);
    await clickWorld([(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...zs) + Math.max(...zs)) / 2]);
    await page.waitForSelector(".prop-title:has-text('Window')");
    assert.match(await page.locator("#prop-width").inputValue(), /1800/);
  });

  it("cannot move the model by dragging in an elevation", async () => {
    const before = JSON.stringify((await scene()).map((o) => o.vertices));
    const data = await api(`/drawing/${await viewId("South")}`);
    const win = data.projection.items.find((i) => i.id === ids.window);
    const c = [(Math.min(...win.tris.flatMap((t) => [t[0], t[2], t[4]])) + Math.max(...win.tris.flatMap((t) => [t[0], t[2], t[4]]))) / 2, 1500];
    const box = await canvas().boundingBox(), [x, y] = await screen2d(c);
    await page.mouse.move(box.x + x, box.y + y); await page.mouse.down(); await page.mouse.move(box.x + x + 60, box.y + y + 30, { steps: 6 }); await page.mouse.up(); await settle();
    assert.equal(JSON.stringify((await scene()).map((o) => o.vertices)), before);
  });

  it("creates a section by clicking two points on the plan, opens it, and cuts the walls it crosses", async () => {
    await openFromTree("Level 01");
    await fit();
    await tool("Section");
    await clickWorld([5500, -400]); await clickWorld([5500, 5400]);        // just outside the walls, but inside the fitted view
    await tab("Section A-A").waitFor();
    const views = (await tree()).filter((n) => n.view_kind === "section");
    assert.deepEqual(views.map((v) => v.label), ["Section A-A"]);
    ids.section = views[0].id;
    const data = await api(`/drawing/${ids.section}`);
    const cut = data.projection.items.filter((i) => i.cut.length).map((i) => i.id);
    assert.ok(cut.includes(walls[0]) && cut.includes(walls[2]), "the south and north walls are cut");
    assert.ok(!cut.includes(walls[1]) && !cut.includes(walls[3]), "the east and west walls are not");
    assert.ok(await page.getByText("Section A-A").first().isVisible());
  });

  it("draws the cut solid and the room open on the canvas", async () => {
    await fit();
    const data = await api(`/drawing/${ids.section}`);
    const southWall = data.projection.items.find((i) => i.id === walls[0]);
    const loop = southWall.cut[0];
    const cx = loop.reduce((a, p) => a + p[0], 0) / loop.length, cy = loop.reduce((a, p) => a + p[1], 0) / loop.length;
    // A cut can be hatched (thin antialiased lines with paper between them), so the cut is ink if some pixel around its middle is clearly darker than paper (255),
    // not if the one pixel at the centre is dark.
    assert.ok(await darkestAround([cx, cy]) < 140, "the cut wall is drawn in ink (solid, or the lines of its hatch)");
    const [[x0], [x1]] = data.projection.bounds;
    assert.ok(luma(await pixel([(x0 + x1) / 2, 1500])) > 200, "the inside of the room is open");
  });

  it("shows the section line on the plan, and double-clicking it opens the section", async () => {
    await openFromTree("Level 01");
    const marks = (await api(`/drawing/${await viewId("Level 01")}`)).section_marks;
    assert.equal(marks.length, 1);
    assert.equal(marks[0].looking, "west");
    await fit();
    await tool("Select");
    await page.keyboard.press("Escape");
    // a point on the line outside the building (the line runs from y = -400 to 5400 mm), so no wall is under it
    await clickWorld([5500, -300], { double: true });
    await page.waitForSelector(`[role=tab][aria-selected=true]:has-text("Section A-A")`);
  });

  it("renames the section and flips its direction in Properties", async () => {
    const name = page.locator("#prop-name");
    await name.fill("Cross section"); await name.press("Enter"); await settle();
    await tab("Cross section").waitFor();
    assert.ok((await tree()).some((n) => n.label === "Cross section"));
    await page.locator("#prop-looking").selectOption("flip"); await settle();
    const view = (await tree()).find((n) => n.label === "Cross section");
    assert.equal((await api(`/properties`, { method: "POST", body: JSON.stringify({ view: view.id }) })).groups[0].rows[0].choices[0].label, "Toward east");
    const data = await api(`/drawing/${view.id}`);
    const seen = data.projection.items.filter((i) => i.tris.length).map((i) => i.id);
    assert.ok(seen.includes(walls[1]) && !seen.includes(walls[3]), "now the east wall is what is seen beyond the cut");
    await page.locator("#prop-depth").fill("2"); await page.locator("#prop-depth").press("Enter"); await settle();
    assert.equal((await api(`/drawing/${view.id}`)).projection.far, 2000);
  });

  it("refuses a duplicate name with a readable message and keeps the old one", async () => {
    const name = page.locator("#prop-name");
    await name.fill("South"); await name.press("Enter"); await settle();
    await page.getByText(/already exists/).first().waitFor();
    assert.ok((await tree()).some((n) => n.label === "Cross section"));
    problems.splice(0, problems.length, ...problems.filter((p) => !p.includes("409")));      // the refusal is the point of this test
    assert.equal(await page.locator("#prop-name").inputValue(), "Cross section");
  });

  it("deletes a view, brings it back with undo, and can re-create a deleted elevation from the menu", async () => {
    await openFromTree("North");
    await tool("Delete view");
    assert.ok(!(await tree()).some((n) => n.label === "North"));
    await page.keyboard.press("Control+z"); await settle();
    assert.ok((await tree()).some((n) => n.label === "North"), "undo brings the view back");
    await page.keyboard.press("Control+Shift+z"); await settle();
    assert.ok(!(await tree()).some((n) => n.label === "North"), "redo removes it again");
    await tool("Elevation");
    await page.getByRole("menuitem", { name: "North" }).click(); await settle();
    assert.ok((await tree()).some((n) => n.label === "North"));
    await tab("North").waitFor();
  });

  it("opens a door schedule from the menu, sorts it, selects an element from a row, and exports CSV", async () => {
    await tool("Schedule");
    await page.getByRole("menuitem", { name: "Door Schedule" }).click();
    await page.locator("table.schedule tbody tr").first().waitFor(); await settle();
    const rows = page.locator("table.schedule tbody tr");
    assert.equal(await rows.count(), 2);
    assert.deepEqual(await page.locator("table.schedule thead th").allInnerTexts(), ["No.", "Name", "Level", "Host wall", "Width (mm)", "Height (mm)", "Area (m²)", "Material", "Hinge", "Swing", "Assembly Code (UniFormat)", "OmniClass T21", "OmniClass T23", "IFC Type Product", "IFC PredefinedType", "NL-SfB"]);
    assert.match(await page.locator("table.schedule tfoot").innerText(), /Total\s+3\.99/);
    const first = async () => (await rows.first().locator("td").nth(4).innerText());
    assert.equal(await first(), "900");
    await page.getByRole("button", { name: /^Width/ }).click();
    await page.getByRole("button", { name: /^Width/ }).click();                       // descending
    assert.equal(await first(), "1000");
    assert.equal(await page.locator("th[aria-sort=descending]").count(), 1);
    await rows.first().click(); await settle();
    await page.waitForSelector(".prop-title:has-text('Door')");
    assert.equal(await rows.first().getAttribute("aria-selected"), "true");
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).first().click()]);
    assert.match(download.suggestedFilename(), /^door schedule\.csv$/i);
    const text = readFileSync(await download.path(), "utf8");
    assert.ok(text.startsWith("﻿No.,Name,Level,Host wall,Width (mm)"));
    assert.equal(text.trim().split("\r\n").length, 4, "header, two doors and the total");
  });

  it("follows the model: a door added later appears in the schedule", async () => {
    await cmd("model.add_door", { wall: walls[1], point: [8000, 2500], width: 800, height: 2000 });
    await page.reload(); await settle();                                  // the browser learns of an outside change by reloading
    await openFromTree("Door Schedule");
    assert.equal(await page.locator("table.schedule tbody tr").count(), 3);
    assert.match(await page.locator("table.schedule tfoot").innerText(), /Total\s+5\.59/);
  });

  it("places the plan, the section, an elevation and a schedule on a sheet", async () => {
    await page.getByRole("tab", { name: "Insert", exact: true }).click();
    await tool("New sheet");
    await page.getByLabel("Sheet name").fill("Documentation"); await page.getByRole("button", { name: "Create" }).click(); await settle();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    for (const label of ["Level 01", "Cross section", "South", "Door Schedule"]) {
      await page.getByRole("button", { name: "Place View" }).click();
      await page.getByRole("menuitem", { name: label, exact: true }).click(); await settle();
    }
    const svg = page.locator(".sheet-wrap svg");
    // The last view takes a moment to be drawn. Each viewport is two nested elements with the same id, so waiting for a fourth *element* returns while only two
    // viewports are there: wait until four different viewport ids exist.
    await page.waitForFunction(() => new Set([...document.querySelectorAll(".sheet-wrap svg [data-viewport]")].map((n) => n.getAttribute("data-viewport"))).size >= 4);
    // Each viewport has an outer and inner SVG group with the same ID; count actual viewports, not DOM wrappers.
    const placedIds = await svg.locator("[data-viewport]").evaluateAll(nodes => [...new Set(nodes.map(node => node.getAttribute("data-viewport")))]);
    assert.equal(placedIds.length, 4);
    assert.ok(await svg.locator("[data-cut]").count() >= 2, "the section's cut is drawn");
    assert.ok(await svg.locator("[data-element]").count() > 4, "the elevation and section surfaces are drawn");
    const text = await svg.innerHTML();
    assert.match(text, /DOOR SCHEDULE/);
    assert.match(text, /LEVEL 01/); assert.match(text, /CROSS SECTION/); assert.match(text, /SOUTH/);
    const sheet = (await tree()).find((n) => n.open?.kind === "sheet" && n.label.includes("Documentation"));
    const data = await api(`/sheet/${sheet.id}`);
    assert.equal(data.viewports.length, 4);
    assert.equal(data.viewports.find((v) => v.view.kind === "schedule").schedule.count, 3);
    assert.equal(data.viewports.find((v) => v.view.kind === "plan").section_marks.length, 1, "the plan on the sheet carries the section line");
  });

  it("places the 3D model, a snapshot of the 3D view and a report on the sheet", async () => {
    await cmd("report.create", { name: "Status", sections: ["summary", "levels"] }); await page.reload(); await settle();
    await openFromTree("3D Model");
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await tool("Snapshot");
    await page.getByRole("menuitem", { name: /On sheet .*Documentation/ }).click(); await settle();
    await page.locator(".sheet-wrap svg image").waitFor();                       // placing the snapshot opens the sheet
    for (const label of [/as vector shapes/, /Report: Status/]) {
      await page.getByRole("button", { name: "Place View" }).click();
      await page.getByRole("menuitem", { name: label }).click(); await settle();
    }
    const sheet = (await tree()).find((n) => n.open?.kind === "sheet" && n.label.includes("Documentation"));
    const data = await api(`/sheet/${sheet.id}`);
    assert.deepEqual(data.viewports.filter((v) => v.kind).map((v) => v.kind).sort(), ["model", "report", "snapshot"]);
    const picture = data.viewports.find((v) => v.kind === "snapshot");
    assert.ok(picture.image.href.length > 500 && picture.image.width_px > 100, "a real picture of the 3D view was stored");
    assert.ok(data.viewports.find((v) => v.kind === "model").solid.faces.length > 20);
    assert.match(data.viewports.find((v) => v.kind === "report").report.lines.map((l) => l.text ?? l.cells?.map((c) => c[1]).join(" ") ?? "").join("\n"), /Level 01/);
    const svg = page.locator(".sheet-wrap svg");
    assert.equal(await svg.locator("image").count(), 1);
    assert.ok(await svg.locator("g[data-viewport] path").count() > 50, "the model's faces are drawn");
    assert.match(await svg.innerHTML(), /Level 01/);
    assert.equal((await api("/check")).ok, true);
  });

  it("will not delete a view that is on a sheet, and says why", async () => {
    await openFromTree("South");
    await tool("Delete view"); await settle();
    await page.getByText(/is placed on sheet/).first().waitFor();
    assert.ok((await tree()).some((n) => n.label === "South"));
    problems.splice(0, problems.length, ...problems.filter((p) => !p.includes("409")));
  });

  it("saves and reopens with its sections, elevations and schedules intact", async () => {
    const saved = (await api("/project/save", { method: "POST", body: JSON.stringify({ path: join(dataDir, "documentation.spadra"), name: "Documentation" }) })).project;
    assert.ok(saved.path && !saved.dirty);
    const before = (await tree()).filter((n) => n.view_kind).map((n) => `${n.view_kind}:${n.label}`).sort();
    await api("/project/open", { method: "POST", body: JSON.stringify({ path: saved.path, discard: true }) });
    assert.deepEqual((await tree()).filter((n) => n.view_kind).map((n) => `${n.view_kind}:${n.label}`).sort(), before);
    const section = (await tree()).find((n) => n.label === "Cross section");
    assert.equal((await api(`/drawing/${section.id}`)).projection.items.filter((i) => i.cut.length).length, 4, "both walls, the window in one, and the floor");
    assert.equal((await api("/check")).ok, true);
  });

  it("shows real Add Layer plus controls for floors and roofs in sections and elevations", async () => {
    await openFromTree("Cross section");
    await tool("Select");
    const section = await api(`/drawing/${await viewId("Cross section")}`);
    const floor = section.projection.items.find((item) => item.category === "floor" && item.cut.length);
    assert.ok(floor, "the section has a cut through the floor");
    // A point that is really inside the cut. The average of a loop's corners is not: for a roof cut (a thick chevron) it falls in the empty space under the ridge.
    const insideOf = (loop) => {
      const crosses = ([x, y]) => loop.reduce((n, [x1, y1], i) => { const [x2, y2] = loop[(i + 1) % loop.length]; return n + ((y1 > y) !== (y2 > y) && x < x1 + ((y - y1) * (x2 - x1)) / (y2 - y1) ? 1 : 0); }, 0) % 2 === 1;
      const clearance = ([x, y]) => Math.min(...loop.map(([x1, y1], i) => { const [x2, y2] = loop[(i + 1) % loop.length]; const dx = x2 - x1, dy = y2 - y1, t = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy || 1))); return Math.hypot(x - x1 - t * dx, y - y1 - t * dy); }));
      const candidates = [];
      for (let i = 0; i < loop.length; i++) for (let j = i + 1; j < loop.length; j++) candidates.push([(loop[i][0] + loop[j][0]) / 2, (loop[i][1] + loop[j][1]) / 2]);
      const inside = candidates.filter(crosses).sort((u, v) => clearance(v) - clearance(u));
      assert.ok(inside.length, "the cut has an inside to click");
      return inside[0];
    };
    await clickWorld(insideOf(floor.cut[0]));
    await page.getByRole("button", { name: "Add top floor layer" }).waitFor();
    await page.getByRole("button", { name: "Add bottom floor layer" }).waitFor();

    const roofLevel = (await api("/project/tree")).levels.find((l) => l.name === "Roof Level").id;
    await cmd("model.add_roof", { level: roofLevel, roof: "gable", points: [[0, 0], [8000, 0], [8000, 5000], [0, 5000]], pitch: 30, thickness: 200 });
    await page.reload(); await settle();
    for (const label of ["Cross section", "South"]) {
      await openFromTree(label);
      const data = await api(`/drawing/${await viewId(label)}`);
      const roof = data.projection.items.find((item) => item.category === "roof" && (item.cut.length || item.tris.length));
      assert.ok(roof, `${label} has real projected roof geometry`);
      const point = roof.cut[0]?.length ? insideOf(roof.cut[0]) : [(roof.tris[0][0] + roof.tris[0][2] + roof.tris[0][4]) / 3, (roof.tris[0][1] + roof.tris[0][3] + roof.tris[0][5]) / 3];
      // Roof and fascia may occupy the same projected surface. Clicking the
      // same point cycles the overlap stack; do not assume the first hit wins.
      for (let attempt = 0; attempt < 8; attempt++) {
        await clickWorld(point);
        if (await page.getByRole("button", { name: "Add outside roof layer" }).count()) break;
      }
      await page.getByRole("button", { name: "Add outside roof layer" }).waitFor();
      await page.getByRole("button", { name: "Add inside roof layer" }).waitFor();
    }
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, []);
  });
});
