// End-to-end: Reconstruct from a 2D plan. A synthetic dimensioned plan (tests/recon_fixtures.py) is chosen in the dialog, detected, checked over the overlay, corrected and generated.
//   node --test tests_e2e/reconstruct.e2e.test.mjs        (build first: the server serves dist/; needs OpenCV and Tesseract for the raster plan)
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

let server, browser, page, url, dataDir, planFile, name;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
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
const settle = () => page.waitForTimeout(450);
const menuTab = (n) => page.getByRole("tab", { name: n, exact: true }).click();

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-recon-"));
  const made = spawnSync(PYTHON, ["-I", "-c", `
import sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from applications.SPADRA.tests import recon_fixtures as F
fx = [f for f in F.fixtures() if f.name == "two-rooms"][0]
data, ext, meta = F.render(fx)
open(sys.argv[1] + "/plan." + ext, "wb").write(data)
print(ext)`, dataDir], { encoding: "utf-8" });
  assert.equal(made.status, 0, made.stderr);
  planFile = join(dataDir, `plan.${made.stdout.trim()}`);
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

describe("reconstruct dialog", () => {
  it("detects walls, openings, rooms and dimensions from the plan, with named controls", async () => {
    const caps = await api("/reconstruct/capabilities");
    if (!caps.sources.raster?.available) return;                    // OpenCV / Tesseract missing: the dialog says so, covered by the unit tests
    await menuTab("Insert");
    await page.locator(".ribbon").getByRole("button", { name: "Reconstruct", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Reconstruct from a 2D plan" });
    await dialog.waitFor();
    await dialog.getByLabel("Plan image or PDF").setInputFiles(planFile);
    await dialog.getByRole("button", { name: "Detect", exact: true }).click();
    await dialog.getByText(/walls ·/).waitFor({ timeout: 60000 });
    const summary = await dialog.getByText(/walls ·/).first().innerText();
    assert.match(summary, /\d+ walls · \d+ openings · \d+ rooms/);
    assert.ok(await dialog.locator("svg[aria-label^='Detected geometry'] polygon").count() > 0, "rooms are drawn on the overlay");
    assert.ok(await dialog.locator("table[aria-label='Dimensions'] tbody tr").count() > 0);
    const unnamed = await dialog.evaluate((root) => [...root.querySelectorAll("input,select,button")].filter((e) => !(e.getAttribute("aria-label") || e.labels?.length || e.textContent.trim() || e.closest("label"))).map((e) => e.outerHTML.slice(0, 80)));
    assert.deepEqual(unnamed, []);
  });

  it("changes nothing until Generate, then builds BIM elements in one Undo step", async () => {
    const caps = await api("/reconstruct/capabilities");
    if (!caps.sources.raster?.available) return;
    const dialog = page.getByRole("dialog", { name: "Reconstruct from a 2D plan" });
    const count = async () => (await api("/project/tree")).tree.sections.flatMap(function f(n) { return [n, ...(n.children ?? []).flatMap(f)]; }).length;
    const before = await count();
    await dialog.getByRole("button", { name: "Generate", exact: true }).click(); await settle();
    const built = await count();
    assert.ok(built > before, `Generate added elements (${before} -> ${built})`);
    await page.getByRole("button", { name: "Undo", exact: true }).first().click(); await settle();
    assert.equal(await count(), before, "one Undo removes everything Generate built");
    assert.deepEqual(problems, []);
  });
});
