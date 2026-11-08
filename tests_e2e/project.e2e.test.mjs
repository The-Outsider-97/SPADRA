// End-to-end tests of the project properties, backups and recovery in a real browser against the real server.
//   SPADRA_E2E_ROOT=<a SLAI root> node --test tests_e2e/project.e2e.test.mjs      (build first: the server serves dist/)
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

let server, browser, page, url, dataDir, engine = false;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-proj-"));
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
const settle = () => page.waitForTimeout(500);
const overlay = () => page.locator(".view-overlay").first();
const click3d = async (p) => {
  const box = await overlay().boundingBox();
  const s = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), p);
  assert.ok(s, `point ${p} is on screen`);
  await page.mouse.move(box.x + s[0], box.y + s[1], { steps: 3 });
  await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
};
const quantitiesButton = () => page.getByRole("button", { name: "Quantities", exact: true });
const analyzeTab = () => page.getByRole("tab", { name: "Analyze", exact: true });
const table = async (label) => page.locator(`dialog table[aria-label='${label}'] tbody tr`).evaluateAll((rows) => rows.map((r) => [...r.querySelectorAll("th,td")].map((c) => c.textContent)));
const closeDialog = async () => { await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle(); };


const menu = async (item) => { await page.getByRole("button", { name: "Project menu" }).click(); await page.getByRole("menuitem", { name: item, exact: true }).click(); await settle(); };
const bannerName = () => api("/bootstrap").then((b) => b.project.name);
let level;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  level = (await api("/project/tree")).levels[0].id;
});
after(async () => { await browser?.close(); server?.kill(); if (dataDir) rmSync(dataDir, { recursive: true, force: true }); });

describe("project properties, backups and recovery", () => {
  it("shows an unsaved project honestly: no path, no files, no backups, and recovery is disabled", async () => {
    await menu("Project properties…");
    const dlg = page.getByRole("dialog", { name: "Project properties" });
    await dlg.waitFor();
    assert.match(await dlg.innerText(), /Not saved yet/);
    assert.match(await dlg.innerText(), /Backups start once the project has been saved/);
    assert.equal(await page.locator("#project-open-recover").isDisabled(), true);
    await dlg.getByRole("button", { name: "Cancel" }).click(); await settle();
  });

  it("renames and describes the project, and one Undo reverts both", async () => {
    await menu("Project properties…");
    await page.locator("#project-name").fill("Harbour Pavilion");
    await page.locator("#project-description").fill("Phase two of the harbour scheme");
    await page.getByRole("button", { name: "Apply" }).click(); await settle();
    assert.equal(await bannerName(), "Harbour Pavilion");
    await menu("Project properties…");
    assert.equal(await page.locator("#project-description").inputValue(), "Phase two of the harbour scheme");
    await page.getByRole("button", { name: "Cancel" }).click(); await settle();
    await page.keyboard.press("Control+z"); await settle();
    assert.equal(await bannerName(), "Untitled project");
  });

  it("refuses an empty name inside the dialog and sends nothing", async () => {
    const before = (await api("/bootstrap")).project.revision;
    await menu("Project properties…");
    await page.locator("#project-name").fill("   ");
    await page.getByRole("button", { name: "Apply" }).click(); await settle();
    assert.match(await page.getByRole("dialog", { name: "Project properties" }).innerText(), /needs a name/);
    await page.getByRole("button", { name: "Cancel" }).click(); await settle();
    assert.equal((await api("/bootstrap")).project.revision, before);
  });

  it("makes a backup on the second save and lists it in the properties", async () => {
    await page.getByRole("button", { name: "Project menu" }).click();
    await page.getByRole("menuitem", { name: "Save", exact: true }).click(); await settle();
    const first = await api("/project/properties");
    assert.deepEqual(first.backups, []);
    await cmd("model.add_wall", { start: [0, 0], end: [4000, 0], level, thickness: 200, height: 3 });
    await menu("Save");
    const second = await api("/project/properties");
    assert.equal(second.backups.length, 1);
    await menu("Project properties…");
    const text = await page.getByRole("dialog", { name: "Project properties" }).innerText();
    assert.match(text, /1 backup /);
    assert.match(text, /model\.sgeo/);
    assert.match(text, /wall: 1/);
    assert.equal(await page.locator("#project-open-recover").isDisabled(), false);
    await page.getByRole("button", { name: "Cancel" }).click(); await settle();
  });

  it("recovers the backup as a new unsaved copy and leaves the saved project and its backup untouched", async () => {
    const saved = await api("/project/properties");
    await cmd("model.add_wall", { start: [0, 2000], end: [4000, 2000], level, thickness: 200, height: 3 });   // unsaved work: the dialog must ask first
    await page.reload(); await settle();                                                                             // the page learns of the API-made edit
    await menu("Recover from a backup…");
    await page.getByRole("dialog", { name: "Unsaved changes" }).waitFor();
    await page.getByRole("button", { name: "Discard changes" }).click(); await settle();
    const list = page.getByRole("dialog", { name: "Recover from a backup" });
    await list.waitFor();
    await list.locator("button[data-backup]").first().click(); await settle();
    const now = await api("/project/properties");
    assert.equal(now.path, null);
    assert.equal(now.dirty, true);
    assert.match(now.name, /\(recovered\)$/);
    assert.equal(now.counts.elements.wall ?? 0, 0, "the backup is the version before the wall was added");
    assert.notEqual(now.id, saved.id);
    const again = await api("/project/open", { method: "POST", body: JSON.stringify({ path: saved.path, discard: true }) });
    assert.equal(again.name, saved.name);
    const reopened = await api("/project/properties");
    assert.equal(reopened.counts.elements.wall, 1, "the saved project still has its wall");
    assert.equal(reopened.backups.length, 1, "and still its backup");
  });

  it("opens the keyboard shortcut list with ? and from the File menu, but not while typing in a field", async () => {
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("?"); await settle();
    const dlg = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    await dlg.waitFor();
    assert.match(await dlg.innerText(), /Ctrl\+Shift\+S/);
    assert.match(await dlg.innerText(), /Play or pause the animation/);
    await dlg.locator(".btn", { hasText: "Close" }).click(); await settle();
    await menu("Project properties…");
    await page.locator("#project-description").focus();
    await page.keyboard.type("what? really?");
    assert.equal(await page.locator("#project-description").inputValue().then((v) => v.endsWith("what? really?")), true);
    assert.equal(await page.getByRole("dialog", { name: "Keyboard shortcuts" }).count(), 0, "typing a question mark in a text box does not open the list");
    await page.getByRole("button", { name: "Cancel" }).click(); await settle();
    await menu("Keyboard shortcuts…");
    await page.getByRole("dialog", { name: "Keyboard shortcuts" }).waitFor();
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  });


  it("places Modifier between Model and Annotate and relocates the requested tools", async () => {
    const labels = (await page.locator(".menubar [role='tab']").allTextContents()).map(s => s.trim());
    assert.deepEqual(labels.slice(labels.indexOf("Model"), labels.indexOf("Annotate") + 1),
                     ["Model", "Modifier", "Annotate"]);
    const tools = (await page.locator(".ribbon .tool").allTextContents()).map(s => s.trim());
    await page.getByRole("tab", { name: "Modifier", exact: true }).click(); await settle();
    const modifiers = (await page.locator(".ribbon .tool").allTextContents()).map(s => s.trim());
    for (const name of ["Boolean", "Trim", "Split", "Chamfer", "Weld", "Subdivision", "Bend",
                        "Array", "Release array", "Extrude", "Void", "Sweep", "Make void"]) {
      assert.ok(modifiers.includes(name), name + " belongs to Modifier");
    }
    await page.getByRole("tab", { name: "Annotate", exact: true }).click(); await settle();
    const annotations = (await page.locator(".ribbon .tool").allTextContents()).map(s => s.trim());
    for (const name of ["Line", "Polyline", "Rectangle", "Circle", "Offset", "Fillet"])
      assert.ok(annotations.includes(name), name + " belongs to Annotate");
    await page.getByRole("tab", { name: "Model", exact: true }).click(); await settle();
    const model = (await page.locator(".ribbon .tool").allTextContents()).map(s => s.trim());
    for (const name of [...modifiers, ...annotations])
      assert.ok(!model.includes(name), name + " should no longer belong to Model");
  });

  it("shows cover, ToC destinations and content-page numbering for a real SLDC report", async () => {
    const made = await cmd("report.create", {
      name: "Pagination QA", author: "Test Author", paper: "A4",
      sections: ["summary", "levels", "schedules"],
    });
    const report = made.id;
    const native = await api(`/report/${report}`);
    assert.equal(native.document.metadata.author, "Test Author");
    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: "Pagination QA", exact: true }).click(); await settle();
    const dialog = page.getByRole("dialog", { name: /Pagination QA/ });
    await dialog.waitFor();
    const pages = dialog.locator(".report-preview .report-page");
    assert.ok(await pages.count() >= 3);
    assert.equal(await pages.first().locator(".report-cover-content h1").innerText(), "SPADRA");
    assert.match(await pages.first().innerText(), /Test Author/);
    assert.equal(await pages.nth(1).locator(".report-toc-title").innerText(), "Table of Contents");
    const contents = dialog.locator(".report-page.report-page-body");
    const total = await contents.count();
    assert.ok(total >= 1);
    assert.equal(await contents.first().locator(".report-page-number").innerText(), `Page 1 of ${total}`);
    const targets = await dialog.locator(".report-toc-entry").evaluateAll(rows => rows.map(row => row.getAttribute("href")?.slice(1)));
    assert.ok(targets.length);
    for (const target of targets)
      assert.equal(await dialog.locator("[id='" + target + "']").count(), 1);
    await dialog.getByRole("button", { name: "Close" }).click(); await settle();
  });

  it("had no console errors or page errors", () => { assert.deepEqual(problems, []); });
});
