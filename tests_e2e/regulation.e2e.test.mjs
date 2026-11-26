// End-to-end test of the Regulations and History windows in a real browser against the real server.
//   SPADRA_E2E_ROOT=<SLAI root> node --test tests_e2e/regulation.e2e.test.mjs      (build first: the server serves dist/)
// A door that is 800 mm wide with no frame is 50 mm under the 850 mm minimum. The test turns the check on, finds the finding, selects the door from it,
// previews the correction (nothing changes), applies it with the explicit Apply, and reads the history back and verifies it.
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.SPADRA_E2E_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir, level, wall, door;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-reg-"));
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
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
const cmd = async (op, params) => (await post("/command", { op, params })).result;
const dialog = () => page.locator("dialog[open]");
const idle = (d) => d.getByText(/^(Saving the settings|Preparing the correction|Applying the correction|Checking)…$/).waitFor({ state: "detached", timeout: 60000 });
const openTool = async (name) => { await page.getByRole("tab", { name: "Analyze", exact: true }).click(); await page.getByRole("button", { name, exact: true }).first().click(); };

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ ...(process.env.SPADRA_E2E_CHROME ? { executablePath: process.env.SPADRA_E2E_CHROME } : {}), args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/status of (400|409|503)/.test(m.text())) problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await page.waitForTimeout(450);
  level = (await api("/project/tree")).levels[0].id;
  wall = (await cmd("model.add_wall", { start: [0, 0], end: [9000, 0], level, thickness: 250, height: 3.2 })).id;
  door = (await cmd("model.add_door", { wall, offset: 3.0, width: 800, height: 2300 })).id;
  await page.reload(); await page.waitForTimeout(900);
});

after(async () => {
  assert.deepEqual(problems, [], "no console errors or page errors");
  await browser?.close();
  if (server && server.exitCode === null) {
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 5000);
      server.once("exit", () => { clearTimeout(timer); resolve(); });
      if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(server.pid), "/t", "/f"], { stdio: "ignore" });
      else server.kill();
    });
  }
  if (dataDir) rmSync(dataDir, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 });
});

describe("Regulations and History", () => {
  it("is off until the project turns it on, and says so", async () => {
    await openTool("Regulations");
    const d = dialog();
    await d.getByText("Regulation assistance is off for this project.").waitFor();
    assert.equal(await d.getByRole("button", { name: "Preview a correction" }).count(), 0);
  });

  it("finds the 800 mm door, with its source, and selects it from the finding", async () => {
    const d = dialog();
    await d.getByLabel("Check this project against the building rules").check();
    await d.locator("select").nth(0).selectOption("NL");
    await d.locator("input[type=date]").fill("2026-11-26");
    await d.getByRole("button", { name: "Save settings" }).click(); await idle(d);
    await d.locator("select").nth(1).selectOption({ index: 1 });
    const fn = d.locator("select").nth(2);
    await fn.selectOption({ label: (await fn.locator("option").allInnerTexts()).find((t) => /kantoor/i.test(t)) });
    await d.getByRole("button", { name: "Save settings" }).click(); await idle(d);
    const rows = d.locator("table[aria-label=Findings] tbody tr");
    const text = await rows.first().innerText();
    assert.match(text, /Does not comply/); assert.match(text, /800 mm/); assert.match(text, /850 mm/); assert.match(text, /4\.180/);
    await rows.first().getByRole("button", { name: "Show" }).click();
    await d.locator(".reg-detail").waitFor();
    await page.waitForFunction(() => document.body.innerText.includes("Single Flush Door") || document.body.innerText.includes("Door"));
    assert.match(await d.locator(".reg-detail").innerText(), /rule data not yet checked against the official text/, "an unverified dataset is labelled as such");
  });

  it("offers a correction only when the person allows it for unchecked rule data, previews it without changing anything, then applies it on Apply", async () => {
    const d = dialog();
    assert.equal(await d.getByRole("button", { name: "Preview a correction" }).count(), 0, "no correction is offered on unverified rule data by default");
    await d.getByLabel("Offer corrections even when").check();
    await d.getByRole("button", { name: "Save settings" }).click(); await idle(d);
    await d.locator("table[aria-label=Findings] tbody tr").first().getByRole("button", { name: "Show" }).click();
    const before = (await api("/regulation")).revision;
    await d.getByRole("button", { name: "Preview a correction" }).click();
    await d.locator(".ap-proposal").waitFor({ timeout: 60000 });
    assert.equal((await api("/regulation")).revision, before, "previewing changes nothing");
    assert.equal((await api("/regulation")).report.summary.non_compliant, 1);
    await d.getByRole("button", { name: "Apply" }).click(); await idle(d);
    const after = await api("/regulation");
    assert.ok(after.revision > before, "Apply made the change");
    assert.equal(after.report.summary.non_compliant, 0, "the door no longer fails");
    await page.keyboard.press("Escape");
  });

  it("shows the correction in the history, in words, and the record verifies", async () => {
    await openTool("History");
    const d = dialog();
    await d.getByText("Correction applied").first().waitFor();
    await d.getByRole("button", { name: "Verify the history" }).click();
    await d.getByText(/consistent and unchanged since they were written/).waitFor();
    await page.keyboard.press("Escape");
  });
});
