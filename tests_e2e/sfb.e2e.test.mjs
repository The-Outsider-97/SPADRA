// End-to-end test of the built-in NL-SfB classification: select an element, search the code list by a Dutch word, set a code, and see a code
// the table does not have refused. Real input in a real browser.
//   node --test tests_e2e/sfb.e2e.test.mjs        (build first: the server serves dist/)
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-sfb-"));
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
const command = (op, params) => api("/command", { method: "POST", body: JSON.stringify({ op, params }) });

const settle = () => page.waitForTimeout(350);
const planId = async () => (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
const drawing = async () => api(`/drawing/${await planId()}`);
const tool = (name) => page.getByRole("button", { name, exact: true }).first().click();
const canvas = () => page.locator("canvas[aria-label='Drawing canvas']");
const view = (fn, arg) => canvas().evaluate((el, [f, a]) => new Function("v", "a", `return (${f})(v, a)`)(el.spadraView, a), [fn.toString(), arg]);

/** Screen position of a world point (drawing units) in the plan. */
const screen = async ([x, y]) => {
  const box = await canvas().boundingBox();
  const s = await view((v, p) => v.screenOf(p), [x, y]);
  return [box.x + s[0], box.y + s[1]];
};
const clickAt = async (world) => { const [x, y] = await screen(world); await page.mouse.move(x, y, { steps: 3 }); await page.mouse.click(x, y); await settle(); };
const dragFrom = async (from, to) => {
  await page.mouse.move(from[0], from[1], { steps: 3 }); await page.mouse.down();
  await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 }); await page.mouse.move(to[0], to[1], { steps: 4 });
  await page.mouse.up(); await settle();
};
const walls = async () => {
  const seen = new Map();
  for (const m of (await drawing()).model) if (m.category === "wall" && !seen.has(m.id)) seen.set(m.id, m.axis);
  return [...seen].map(([id, axis]) => ({ id, ...axis }));
};
const near = (a, b, tol = 1e-6) => Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol;
const handles = () => view((v) => v.handleScreens());
const box = async () => canvas().boundingBox();

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  if (process.env.SPADRA_DEBUG) page.on("response", async (r) => { if (r.status() >= 400) console.error("HTTP", r.status(), r.request().method(), r.url().split("/api")[1], r.request().postData()?.slice(0, 160), (await r.text()).slice(0, 200)); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});


const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const sfbRow = (p) => p.groups.flatMap((g) => g.rows).find((r) => r.key === "sfb");

describe("NL-SfB codes in a browser", () => {
  let wall;
  it("lists the codes that match a Dutch word while typing in the Properties panel", async () => {
    const level = (await drawing()).view.level_id;
    wall = (await command("model.add_wall", { level, start: [0, 0], end: [4000, 0], height: 3, thickness: 200 })).result.id;
    await page.reload(); await settle();
    await page.getByRole("button", { name: "2D", exact: true }).click(); await settle();
    await page.keyboard.press("v");
    await clickAt([2000, 0]);
    const field = page.getByLabel(/NL-SfB code/).first();
    await field.waitFor();
    await field.fill("buitenwand"); await page.waitForTimeout(600);
    const options = await page.locator("datalist option").evaluateAll((os) => os.map((o) => ({ value: o.getAttribute("value"), text: o.textContent })));
    assert.ok(options.some((o) => o.value === "(21)" && /Buitenwanden/.test(o.text ?? "")), `the list offers (21) Buitenwanden (${JSON.stringify(options.slice(0, 3))})`);
  });

  it("sets a code the table has, shows its name, and keeps it", async () => {
    const field = page.getByLabel(/NL-SfB code/).first();
    await field.fill("(21)"); await field.press("Enter"); await settle();
    const row = sfbRow(await props(wall));
    assert.equal(row.value, "(21)");
    assert.match(row.text, /Buitenwanden/);
    assert.match(await page.locator(".sfb-name").first().textContent(), /Buitenwanden/, "the name of the code is shown under the field");
  });

  it("refuses a code the table does not have and leaves the one that is set", async () => {
    const field = page.getByLabel(/NL-SfB code/).first();
    await field.fill("(99.99)"); await field.press("Enter"); await settle();
    assert.equal(sfbRow(await props(wall)).value, "(21)");
  });

  it("clears the code", async () => {
    await page.getByRole("button", { name: /Clear NL-SfB/ }).first().click(); await settle();
    assert.equal(sfbRow(await props(wall)).value, "");
  });

  it("raised no errors in the browser except the refused code", () => {
    assert.deepEqual(problems.filter((p) => !/40\d|Failed to load resource/.test(p)), []);
  });
});
