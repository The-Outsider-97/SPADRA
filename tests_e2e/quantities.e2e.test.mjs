// End-to-end tests of the quantities take-off in a real browser against the real server.
//   SPADRA_E2E_ROOT=<a SLAI root> node --test tests_e2e/quantities.e2e.test.mjs      (build first: the server serves dist/)
// The steel-density test needs the SLAI STEM Agent and skips without it.
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-qty-"));
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

let wall, beam;
before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  engine = !!(await api("/bootstrap")).capabilities.engineering;
  const level = (await api("/project/tree")).levels[0].id;
  wall = (await cmd("model.add_wall", { start: [0, 0], end: [4000, 0], level, thickness: 200, height: 3 })).id;
  await cmd("model.add_door", { wall, offset: 3000, width: 900, height: 2100 });
  beam = (await cmd("model.add_beam", { start: [0, 3000], end: [6000, 3000], level, top: 3, width: 200, depth: 400, material: "Steel" })).id;
  await page.reload(); await settle();
  await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
});
after(async () => { await browser?.close(); server?.kill(); if (dataDir) rmSync(dataDir, { recursive: true, force: true }); });

describe("quantities", () => {
  it("measures the whole model when nothing is selected: the wall's volume has its door taken out (wall, door and beam are three elements)", async () => {
    await analyzeTab().click();
    await quantitiesButton().click();
    await page.locator("dialog table[aria-label='Quantities']").waitFor();
    const rows = await table("Quantities");
    const w = rows.find((r) => r[0] === "Basic Wall");
    assert.equal(w[3], String(Number((4 * 0.2 * 3 - 0.9 * 0.2 * 2.1).toPrecision(4))), "2.022 m³");
    assert.equal(w[5], "no density");
    assert.match(await page.locator("dialog tfoot").innerText(), /3 of 3 measured/);
    assert.match(await page.locator("dialog").innerText(), /Mass needs a density/);
    await closeDialog();
  });

  it("gives a mass once a density is entered, and labels the centre accordingly", async () => {
    await analyzeTab().click(); await quantitiesButton().click();
    await page.locator("#density-Concrete").waitFor();
    assert.match(await page.locator("dialog tfoot").innerText(), /centre of volume|centre of mass/);
    await page.locator("#density-Concrete").fill("2400");
    await page.locator("#quantities-recalc").click(); await settle();
    const rows = await table("Quantities");
    const w = rows.find((r) => r[0] === "Basic Wall");
    assert.equal(w[5], String(Number((2.022 * 2400).toPrecision(5))));
    assert.deepEqual(await table("Material quantities").then((t) => t.map((r) => r[0])).catch(() => []), ["Concrete", "Steel", "Timber"]);
    await closeDialog();
  });

  it("uses the SLAI engine's density for steel and says so (needs the STEM Agent)", async (t) => {
    if (!engine) return t.skip("the SLAI STEM Agent is not importable here");
    const q = await api("/quantities", { method: "POST", body: JSON.stringify({ ids: [beam] }) });
    assert.equal(q.rows[0].density_source, "the SLAI engine's value");
    assert.equal(q.rows[0].density_kg_m3, 7850);
    assert.ok(Math.abs(q.rows[0].mass_kg - 6 * 0.2 * 0.4 * 7850) < 1e-6);
  });

  it("measures just the selection", async () => {
    const box = await page.locator(".view-overlay").first().boundingBox();
    const s = await page.locator(".view-overlay").first().evaluate((el, q) => el.spadraView.screenOf(q), [2, 0, 1.0]);
    await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
    await analyzeTab().click(); await quantitiesButton().click();
    await page.locator("dialog table[aria-label='Quantities']").waitFor();
    assert.equal((await table("Quantities")).length, 1);
    assert.match(await page.locator("dialog").innerText(), /1 selected/i);
    await closeDialog();
  });

  it("remembers the densities you entered, applies them on opening, and still lets you change them", async () => {
    await analyzeTab().click(); await quantitiesButton().click();
    await page.locator("#density-Concrete").waitFor();
    assert.equal(await page.locator("#density-Concrete").inputValue(), "2400");
    const rows = await table("Quantities");
    assert.equal(rows.find((r) => r[0] === "Basic Wall")[5], String(Number((2.022 * 2400).toPrecision(5))), "mass is shown without typing anything");
    await page.locator("#density-Concrete").fill("2500");
    await page.locator("#quantities-recalc").click(); await settle();
    assert.equal((await table("Quantities")).find((r) => r[0] === "Basic Wall")[5], String(Number((2.022 * 2500).toPrecision(5))));
    await page.locator("#density-Concrete").fill("2400");
    await page.locator("#quantities-recalc").click(); await settle();
    await closeDialog();
  });

  it("is available from a floor plan and downloads a CSV of what is shown", async () => {
    await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
    await analyzeTab().click();
    assert.equal(await quantitiesButton().isDisabled(), false, "not limited to the 3D tab");
    await quantitiesButton().click();
    await page.locator("dialog table[aria-label='Quantities']").waitFor();
    const [download] = await Promise.all([page.waitForEvent("download"), page.locator("dialog .btn", { hasText: "Download CSV" }).click()]);
    assert.equal(download.suggestedFilename(), "quantities.csv");
    const text = await (async () => { const { readFileSync } = await import("node:fs"); return readFileSync(await download.path(), "utf8"); })();
    assert.ok(text.startsWith("\ufeffElement,Category,Material,Volume (m3)"));
    assert.match(text, /Basic Wall,wall,Concrete,2\.022/);
    assert.match(text, /Total \(3 of 3 measured\)/);
    await closeDialog();
    await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
  });

  it("refuses a bad density with a reason and keeps the dialog open", async () => {
    await analyzeTab().click(); await quantitiesButton().click();
    await page.locator("#density-Concrete").waitFor();
    await page.locator("#density-Concrete").fill("heavy");
    await page.locator("#quantities-recalc").click(); await settle();
    assert.match(await page.locator("dialog [role=alert]").innerText(), /density of Concrete must be a number/);
    await closeDialog();
  });

  it("raised no errors in the browser", async () => { assert.deepEqual(problems, [], problems.join("\n")); });
});
