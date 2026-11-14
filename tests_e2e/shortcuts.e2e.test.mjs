// End-to-end test of the keyboard shortcuts in a real browser against the real server: a key runs its tool, a preset changes the keys, a key is re-recorded and
// kept across a reload, a clash is explained and resolved, reserved keys are refused, and typing in a field never runs a shortcut. Real key presses.
//   node --test tests_e2e/shortcuts.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-keys-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: SLAI, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  return new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error(`SPADRA did not start:\n${out}`)), 20000);
    server.stdout.on("data", (d) => { out += d; const m = /Open\s*:\s*(http\S+)/.exec(out); if (m) { clearTimeout(timer); ok(m[1]); } });
    server.stderr.on("data", (d) => { out += d; });
    server.on("exit", (code) => { clearTimeout(timer); fail(new Error(`SPADRA exited (${code}):\n${out}`)); });
  });
}

const settle = () => page.waitForTimeout(300);
const dialog = () => page.getByRole("dialog", { name: "Keyboard shortcuts" });
const row = (action) => dialog().locator(`.keymap-row[data-action='${action}']`);
const activeTool = async () => ((await page.locator(".ribbon .tool[aria-pressed='true']").first().textContent({ timeout: 1500 }).catch(() => "")) ?? "").trim();
const openList = async () => { await page.locator("body").click({ position: { x: 5, y: 5 } }); await page.keyboard.press("F1"); await dialog().waitFor(); await settle(); };
const closeList = async () => { await dialog().locator(".dialog-actions .btn", { hasText: "Close" }).click(); await settle(); };
const showPlan = async () => {
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
  await page.getByRole("tab", { name: /^Level 01/ }).first().click(); await settle();
};

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  await showPlan();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("the default keys", () => {
  it("a letter selects its tool, and the tool's tooltip names its key", async () => {
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("l"); await settle();
    assert.equal(await activeTool(), "Line");
    assert.match(await page.locator(".ribbon .tool[aria-pressed='true']").first().getAttribute("title") ?? "", /\(L\)/);
    await page.keyboard.press("v"); await settle();
    assert.equal(await activeTool(), "Select");
  });

  it("typing in a text field never runs a shortcut", async () => {
    await openList();
    await dialog().getByLabel("Search shortcuts").click();
    await page.keyboard.type("wall"); await settle();
    assert.equal(await dialog().getByLabel("Search shortcuts").inputValue(), "wall");
    assert.equal(await row("tool.wall").count(), 1, "the search narrowed the list to Wall");
    assert.equal(await row("tool.line").count(), 0);
    await closeList();
    assert.equal(await activeTool(), "Select", "the letters typed in the box did not select tools");
  });

  it("F7 hides and shows the grid, and the choice is kept", async () => {
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("F7"); await settle();
    assert.equal(await page.evaluate(() => localStorage.getItem("spadra.showGrid")), "0");
    await page.getByRole("tab", { name: "View", exact: true }).click(); await settle();
    await page.locator(".ribbon .tool", { hasText: "Grid: hidden" }).waitFor();
    await page.keyboard.press("F7"); await settle();
    assert.equal(await page.evaluate(() => localStorage.getItem("spadra.showGrid")), "1");
    await page.locator(".ribbon .tool", { hasText: "Grid: shown" }).waitFor();
  });
});

describe("presets", () => {
  it("Revit keys: W then A is the wall tool, and the single W no longer is", async () => {
    await openList();
    await dialog().getByLabel("Preset", { exact: true }).selectOption("revit"); await settle();
    assert.match(await row("tool.wall").innerText(), /W then A/);
    await closeList();
    await page.getByRole("tab", { name: "Model", exact: true }).click(); await settle();
    const before = await activeTool();
    await page.keyboard.press("v"); await settle();
    assert.equal(await activeTool(), before, "V is not a Revit key: nothing happened");
    await page.keyboard.press("w"); await page.keyboard.press("a"); await settle();
    assert.equal(await activeTool(), "Wall");
    assert.match(await page.locator(".ribbon .tool[aria-pressed='true']").first().getAttribute("title") ?? "", /\(W A\)/);
  });

  it("AutoCAD keys: P then L is Polyline, R then E then C is Rectangle", async () => {
    await openList();
    await dialog().getByLabel("Preset", { exact: true }).selectOption("autocad"); await settle();
    await closeList();
    await page.keyboard.press("p"); await page.keyboard.press("l"); await settle();
    assert.equal(await activeTool(), "Polyline");
    await page.keyboard.press("r"); await page.keyboard.press("e"); await page.keyboard.press("c"); await settle();
    assert.equal(await activeTool(), "Rectangle");
  });

  it("3ds Max keys: S toggles snap and G toggles the grid", async () => {
    await openList();
    await dialog().getByLabel("Preset", { exact: true }).selectOption("max"); await settle();
    await closeList();
    const snap = await page.evaluate(() => localStorage.getItem("spadra.snapGrid"));
    await page.keyboard.press("s"); await settle();
    assert.notEqual(await page.evaluate(() => localStorage.getItem("spadra.snapGrid")), snap, "S flipped the snap");
    await page.keyboard.press("s"); await settle();
    await page.keyboard.press("g"); await settle();
    assert.equal(await page.evaluate(() => localStorage.getItem("spadra.showGrid")), "0");
    await page.keyboard.press("g"); await settle();
    assert.equal(await page.evaluate(() => localStorage.getItem("spadra.showGrid")), "1");
  });
});

describe("changing a key", () => {
  it("records a new key for an action, uses it, and keeps it after a reload", async () => {
    await openList();
    await dialog().getByLabel("Preset", { exact: true }).selectOption("spadra"); await settle();
    await dialog().getByLabel("Search shortcuts").fill("line"); await settle();
    await row("tool.line").getByRole("button", { name: /^Add a key to Line$/ }).click();
    await page.keyboard.press("Control+Alt+k"); await settle();
    assert.match(await row("tool.line").innerText(), /Ctrl\+Alt\+K/);
    await closeList();
    await page.keyboard.press("Control+Alt+k"); await settle();
    assert.equal(await activeTool(), "Line");
    await page.reload(); await settle(); await showPlan();
    await page.keyboard.press("v"); await page.keyboard.press("Control+Alt+k"); await settle();
    assert.equal(await activeTool(), "Line", "the key survived a reload");
    await openList();
    await dialog().getByLabel("Search shortcuts").fill("line"); await settle();
    assert.equal(await dialog().getByLabel("Preset", { exact: true }).inputValue(), "spadra");
    assert.match(await row("tool.line").innerText(), /Ctrl\+Alt\+K/);
    await closeList();
  });

  it("explains a clash and lets the person take the key or cancel", async () => {
    await openList();
    await dialog().getByLabel("Search shortcuts").fill("line"); await settle();
    await row("tool.line").getByRole("button", { name: /^Add a key to Line$/ }).click();
    await page.keyboard.press("w"); await page.waitForTimeout(1100);
    assert.match(await dialog().locator(".keymap-note").innerText(), /already used by Wall/);
    await dialog().getByRole("button", { name: "Cancel" }).click(); await settle();
    assert.doesNotMatch(await row("tool.line").innerText(), /\bW\b/, "cancelling changed nothing");
    await row("tool.line").getByRole("button", { name: /^Add a key to Line$/ }).click();
    await page.keyboard.press("w"); await page.waitForTimeout(1100);
    await dialog().getByRole("button", { name: "Reassign" }).click(); await settle();
    await dialog().getByLabel("Search shortcuts").fill(""); await settle();
    assert.match(await row("tool.line").innerText(), /\bW\b/);
    assert.doesNotMatch(await row("tool.wall").innerText(), /\bW\b/, "the wall tool gave the key up");
    assert.equal(await dialog().locator(".keymap-conflicts").count(), 0, "no conflict remains");
    await closeList();
    await page.keyboard.press("v"); await page.keyboard.press("w"); await settle();
    assert.equal(await activeTool(), "Line", "W now draws a line");
  });

  it("refuses the keys the views use, and Esc cancels a recording without closing the dialog", async () => {
    await openList();
    await dialog().getByLabel("Search shortcuts").fill("circle"); await settle();
    await row("tool.circle").getByRole("button", { name: /^Add a key to Circle$/ }).click();
    await page.keyboard.press("Delete"); await settle();
    assert.match(await dialog().locator(".keymap-note").innerText(), /cannot be reassigned/);
    await row("tool.circle").getByRole("button", { name: /^Add a key to Circle$/ }).click();
    await page.keyboard.press("Escape"); await settle();
    assert.equal(await dialog().count(), 1, "Esc cancelled the recording and left the dialog open");
    await closeList();
  });

  it("Reset all puts the preset's keys back", async () => {
    await openList();
    await dialog().getByRole("button", { name: "Reset all" }).click(); await settle();
    await dialog().getByLabel("Search shortcuts").fill("wall"); await settle();
    assert.match(await row("tool.wall").innerText(), /\bW\b/);
    await dialog().getByLabel("Search shortcuts").fill("line"); await settle();
    assert.doesNotMatch(await row("tool.line").innerText(), /Ctrl\+Alt\+K/);
    await closeList();
  });

  it("raised no errors in the browser", () => assert.deepEqual(problems, []));
});
