// End-to-end tests of the working surface: hotkeys, levels, families, reports, links in Properties, the Project Browser menu, operations and sync.
//   node --test tests_e2e/workspace.e2e.test.mjs        (build first: the server serves dist/)
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
const ROOT = process.env.SPADRA_E2E_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-ws-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  return new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`SPADRA did not start:\n${out}`)), 30000);
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
const settle = () => page.waitForTimeout(450);
const flat = (nodes) => nodes.flatMap((n) => [n, ...flat(n.children ?? [])]);
const tree = async () => (await api("/project/tree")).tree;
const nodes = async () => flat((await tree()).sections ?? []);
const menuTab = (name) => page.getByRole("tab", { name, exact: true }).click();
const ribbon = (name) => page.locator(".ribbon").getByRole("button", { name, exact: true });
const closeDialog = async () => { await page.keyboard.press("Escape"); await settle(); };

let l1, walls = [], door;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  l1 = (await api("/project/tree")).levels[0].id;
  const room = [[0, 0], [8000, 0], [8000, 5000], [0, 5000]];
  for (let i = 0; i < 4; i++) walls.push((await cmd("model.add_wall", { start: room[i], end: room[(i + 1) % 4], level: l1, thickness: 250, height: 3.2 })).id);
  door = (await cmd("model.add_door", { wall: walls[0], point: [2000, 0], width: 900, height: 2100 })).id;
  await page.reload(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("keyboard", () => {
  it("selects a tool by its letter in a drawing, and not while typing or in a dialog", async () => {
    await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("l");
    assert.equal(await ribbon("Line").getAttribute("aria-pressed"), "true");
    await page.keyboard.press("g");
    assert.equal(await ribbon("Grid").getAttribute("aria-pressed"), "true", "the tool is on the Insert tab, which is shown");
    await page.keyboard.press("l");
    assert.equal(await ribbon("Line").getAttribute("aria-pressed"), "true", "and back to the tab that holds the next tool");
    await page.keyboard.press("v");
    assert.equal(await ribbon("Select").getAttribute("aria-pressed"), "true");
    await page.getByLabel("Search the project").click();
    await page.keyboard.type("l");
    assert.equal(await ribbon("Select").getAttribute("aria-pressed"), "true", "typing a letter in the search box does not change the tool");
    await page.getByLabel("Search the project").fill("");
    await page.keyboard.press("Tab");
    await menuTab("Insert"); await ribbon("Levels").click();
    await page.getByRole("dialog", { name: "Levels" }).waitFor();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("l");
    await closeDialog();
    await menuTab("Model");
    assert.equal(await ribbon("Select").getAttribute("aria-pressed"), "true", "a dialog keeps the keys to itself");
  });

  it("goes to the model and back with 3 and 2, and to the search box with /", async () => {
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("3"); await settle();
    assert.equal(await page.getByRole("button", { name: "3D", exact: true }).getAttribute("aria-pressed"), "true");
    await page.keyboard.press("2"); await settle();
    assert.equal(await page.getByRole("button", { name: "2D", exact: true }).getAttribute("aria-pressed"), "true");
    await page.keyboard.press("/");
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Search the project");
    await page.keyboard.press("Escape");
    await page.locator("body").click({ position: { x: 5, y: 5 } });
  });

  it("pans and zooms the drawing from the keyboard when it has focus", async () => {
    const canvas = page.locator(".view-canvas").first();
    await canvas.focus();
    const before = await canvas.evaluate((el) => ({ scale: el.spadraView.scale, centre: [...el.spadraView.centre] }));
    await page.keyboard.press("ArrowRight"); await page.keyboard.press("+");
    const after = await canvas.evaluate((el) => ({ scale: el.spadraView.scale, centre: [...el.spadraView.centre] }));
    assert.ok(after.centre[0] > before.centre[0], "the view moved right");
    assert.ok(after.scale > before.scale, "the view zoomed in");
    await page.keyboard.press("0");
    const fitted = await canvas.evaluate((el) => el.spadraView.scale);
    assert.ok(fitted < after.scale, "0 fits the drawing again");
  });
});

describe("levels, families and reports", () => {
  it("adds a level from the Levels dialog and the Project Browser lists it", async () => {
    await menuTab("Insert"); await ribbon("Levels").click();
    const dlg = page.getByRole("dialog", { name: "Levels" });
    await dlg.getByLabel("New level name").fill("Mezzanine");
    await dlg.getByLabel("New level elevation in metres").fill("1.5");
    await dlg.getByRole("button", { name: "Add level" }).click(); await settle();
    const names = (await api("/project/tree")).levels.map((l) => l.name);
    assert.deepEqual(names, ["Level 01", "Mezzanine", "Level 02", "Roof Level"], "ordered by elevation");
    assert.ok(await dlg.getByLabel("Name of Mezzanine").count(), "the dialog shows the new level without being reopened");
    await closeDialog();
    await page.getByRole("treeitem", { name: /Mezzanine/ }).first().waitFor();
    await menuTab("Model");
  });

  it("opens the family dialog from the Insert menu and from a placed family's Properties link", async () => {
    const made = await cmd("family.define", { name: "Bollard", shape: "cylinder", parameters: { radius: { default: 150, min: 50, max: 300 }, height: { default: 900 } } });
    const placed = await cmd("family.place", { family: made.id, level: l1, point: [1000, 1000] });
    await menuTab("Insert"); await ribbon("Families").click();
    const dlg = page.getByRole("dialog", { name: "Families" });
    await dlg.getByText("Bollard").first().waitFor();
    await closeDialog();
    await menuTab("Model");
    await page.reload(); await settle();
    await page.getByLabel("Search the project").fill("Bollard");
    await page.getByRole("treeitem", { name: /Bollard/ }).last().click(); await settle();
    await page.getByLabel("Search the project").fill("");
    const link = page.locator(".link-row", { hasText: "Bollard" });
    await link.waitFor();
    await link.click();
    await page.getByRole("dialog", { name: "Families" }).waitFor();
    await closeDialog();
    assert.ok(placed.id);
  });

  it("makes a report as an SLDC document, shows it, and deletes it from the Project Browser menu with Undo bringing it back", async () => {
    await menuTab("Analyze"); await ribbon("Report").click();
    await page.getByRole("dialog", { name: "New report" }).waitFor();
    await page.getByLabel("Report name").fill("Handover status");
    await page.getByRole("button", { name: "Make report" }).click();
    const view = page.getByRole("dialog", { name: "Handover status" });
    await view.waitFor({ timeout: 20000 });
    const text = await view.getByLabel("Text of Handover status").innerText();
    assert.match(text, /Project and model summary/);
    assert.match(text, /What this check does not do/);
    await view.locator(".btn.primary").click(); await settle();
    const entry = (await nodes()).find((n) => n.report);
    assert.ok(entry, "the report is in the Project Browser");
    await page.getByRole("treeitem", { name: /Handover status/ }).click({ button: "right" });
    await page.getByRole("menuitem", { name: /Delete Handover status/ }).click();
    await page.getByRole("button", { name: "Delete", exact: true }).click(); await settle();
    assert.equal((await nodes()).filter((n) => n.report).length, 0);
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await nodes()).filter((n) => n.report).length, 1);
    await menuTab("Model");
  });

  it("rebuilds the model as a background operation, which then shows in Operations", async () => {
    await menuTab("Analyze"); await ribbon("Rebuild").click();
    await page.getByText("Model rebuilt").first().waitFor();
    await menuTab("File").catch(() => undefined);
    await page.getByRole("menuitem", { name: /Operations/ }).click();
    const text = await page.getByRole("dialog", { name: "Operations" }).innerText();
    assert.match(text, /Rebuild the model/);
    await closeDialog();
    await menuTab("Model");
  });
});

describe("properties and documentation", () => {
  it("leads from a door to its host wall with a link in Properties", async () => {
    await page.getByLabel("Search the project").fill((await nodes()).find((n) => n.select?.model === door).label);
    await page.getByRole("treeitem").filter({ hasText: /./ }).first().waitFor();
    await page.locator(`[data-node="${(await nodes()).find((n) => n.select?.model === door).id}"]`).click(); await settle();
    await page.getByLabel("Search the project").fill("");
    const link = page.locator(".link-row", { hasText: /Wall|WL/ }).first();
    await link.waitFor();
    await link.click(); await settle();
    const title = await page.locator("aside, .panel").filter({ hasText: "Properties" }).last().innerText();
    assert.match(title, /Wall|WL/);
  });

  it("issues a revision from the sheet's Revision tool and flags the sheet when the model later changes", async () => {
    const plan = (await nodes()).find((n) => n.open?.kind === "drawing" && n.view_kind === "plan");
    const sheet = await cmd("sheet.create", { name: "Plan" });
    await cmd("sheet.place_view", { id: sheet.id, view_id: plan.open.id });
    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: /A101/ }).first().click(); await settle();
    await ribbon("Revision").click();
    const dlg = page.getByRole("dialog", { name: "Issue a revision" });
    await dlg.getByLabel("Description").fill("First issue");
    await dlg.getByRole("button", { name: "Issue" }).click(); await settle();
    const data = await api(`/sheet/${sheet.id}`);
    assert.equal(data.revisions.length, 1);
    assert.equal(await page.locator(".sync-chip").count(), 0, "everything is in step right after an issue");
    await cmd("model.set_property", { id: door, key: "width", value: 1000 });
    await page.reload(); await settle();
    const chip = page.locator(".sync-chip");
    await chip.waitFor();
    assert.match(await chip.innerText(), /1 sheet changed/);
    await chip.click();
    await page.getByRole("menuitem", { name: /A101/ }).click(); await settle();
    assert.match(await page.locator(".screen-only").first().textContent(), /Changed since revision/);
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, [], problems.join("\n"));
  });
});
