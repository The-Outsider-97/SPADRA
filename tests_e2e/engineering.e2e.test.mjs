// End-to-end tests of beam analysis in a real browser against the real server and the real SLAI STEM Agent.
//   SPADRA_E2E_ROOT=<a SLAI root that has src/agents> node --test tests_e2e/engineering.e2e.test.mjs      (build first: the server serves dist/)
// Where the agent is not importable the server reports the capability as off and these tests skip (they never fake the agent).
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

let server, browser, page, url, dataDir, available = false;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-eng-"));
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
const analyzeTab = () => page.getByRole("tab", { name: "Analyze", exact: true });
const beamButton = () => page.getByRole("button", { name: "Beam analysis", exact: true });
const result = async () => Object.fromEntries(await page.locator("table[aria-label='Beam analysis result'] tr").evaluateAll((rows) => rows.map((r) => [r.querySelector("th").textContent, r.querySelector("td").textContent])));

let steel, timber, wall;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !(/status of 400/.test(m.text()) && /\/api\/engineering\/beam$/.test(m.location().url))) problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  available = !!(await api("/bootstrap")).capabilities.engineering;
  if (!available) return;
  const level = (await api("/project/tree")).levels[0].id;
  steel = (await cmd("model.add_beam", { start: [0, 0], end: [6000, 0], level, top: 3, width: 200, depth: 400, material: "Steel" })).id;
  timber = (await cmd("model.add_beam", { start: [0, 3000], end: [6000, 3000], level, top: 3, width: 200, depth: 400, material: "Timber" })).id;
  wall = (await cmd("model.add_wall", { start: [0, 6000], end: [4000, 6000], level, thickness: 200, height: 3 })).id;
  await page.reload(); await settle();
  await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const ifAvailable = (fn) => async (t) => { if (!available) return t.skip("the SLAI STEM Agent is not importable in this SLAI root"); await fn(t); };

describe("beam analysis", () => {
  it("is off until a beam is selected, and says why", ifAvailable(async () => {
    await analyzeTab().click();
    assert.ok(await beamButton().isDisabled());
    assert.match(await beamButton().getAttribute("title"), /Select a beam first/);
  }));

  it("analyses the selected steel beam with the agent's own figures", ifAvailable(async () => {
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("button", { name: "Select", exact: true }).first().click();
    await click3d([3, 0, 2.8]);
    await analyzeTab().click();
    assert.ok(await beamButton().isEnabled());
    await beamButton().click();
    await page.locator("#beam-load").fill("10");
    await page.getByRole("button", { name: "Analyse" }).click();
    await page.getByRole("heading", { name: "Result" }).waitFor();
    const r = await result();
    assert.equal(r["Maximum bending moment"], "45 kN·m");              // w L^2 / 8 for 10 kN/m over 6 m
    assert.equal(r["Maximum shear force"], "30 kN");                   // w L / 2
    assert.equal(r["Maximum deflection"], "0.791 mm");                 // 5 w L^4 / (384 E I), E = 200 GPa, I = b h^3 / 12
    assert.match(r["Engine deflection check"], /within$/);
    const text = await page.locator("dialog").innerText();
    assert.match(text, /E = 200 GPa, the SLAI engine's value for structural_steel_a36/);
    assert.match(text, /self-weight not included/);
    assert.match(text, /not a code check|not a structural design/i);
    assert.match(text, /Computed by the SLAI STEM Agent/);
  }));

  it("only offers the support and load combinations the engine supports", ifAvailable(async () => {
    await page.locator("#beam-support").selectOption("cantilever");
    assert.ok(await page.locator("#beam-loadtype option[value=point_center]").isDisabled());
    assert.ok(await page.locator("#beam-loadtype option[value=point_end]").isEnabled());
    await page.locator("#beam-loadtype").selectOption("point_end");
    await page.locator("#beam-load").fill("20");
    await page.getByRole("button", { name: "Analyse" }).click();
    await page.waitForFunction(() => document.querySelector("table[aria-label='Beam analysis result']")?.textContent.includes("120 kN"));
    const r = await result();
    assert.equal(r["Maximum bending moment"], "120 kN·m");             // P L
    assert.equal(r["Maximum shear force"], "20 kN");
  }));

  it("refuses a bad load in the dialog without calling the agent", ifAvailable(async () => {
    await page.locator("#beam-load").fill("abc");
    await page.getByRole("button", { name: "Analyse" }).click();
    await page.getByText("Enter the load as a number greater than zero.").waitFor();
    assert.equal(await page.locator("table[aria-label='Beam analysis result']").count(), 0);
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  }));

  it("asks for E for timber instead of assuming one, then uses the entered value", ifAvailable(async () => {
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await click3d([3, 3, 2.8]);
    assert.equal((await api("/properties", { method: "POST", body: JSON.stringify({ model: timber }) })).top.find((r) => r.key === "material").value, "Timber");
    await analyzeTab().click();
    await beamButton().click();
    await page.locator("#beam-load").fill("10");
    await page.getByRole("button", { name: "Analyse" }).click();
    await page.locator("dialog [role=alert]", { hasText: /SPADRA does not assume one/ }).waitFor();
    assert.equal(await page.locator("table[aria-label='Beam analysis result']").count(), 0);
    await page.locator("#beam-modulus").fill("11");
    await page.getByRole("button", { name: "Analyse" }).click();
    await page.getByRole("heading", { name: "Result" }).waitFor();
    assert.match(await page.locator("dialog").innerText(), /E = 11 GPa, entered by you/);
    assert.equal((await result())["Maximum deflection"], "14.38 mm");   // 5 w L^4 / (384 E I) with E = 11 GPa
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  }));

  it("does not offer beam analysis for a wall, and analysis never changes the project", ifAvailable(async () => {
    const before = (await api("/bootstrap")).project;
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await click3d([2, 6, 1.5]);
    await analyzeTab().click();
    assert.ok(await beamButton().isDisabled());
    const after = (await api("/bootstrap")).project;
    assert.equal(after.revision, before.revision);
    assert.equal(after.dirty, before.dirty);
  }));

  it("raised no browser errors (apart from the refusal provoked on purpose)", () => { assert.deepEqual(problems, []); });
});
