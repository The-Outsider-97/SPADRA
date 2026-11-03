// End-to-end tests of import and export in a real browser against the real server and SLAI's own adapters.
//   node --test tests_e2e/interop.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.SPADRA_E2E_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir, interop;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-io-"));
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
const scene = async () => (await api("/scene")).objects;

/** A binary STL of a cube of the given edge, from the origin. */
function cubeStl(edge) {
  const c = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]];
  const quads = [[[0, 3, 2, 1], [0, 0, -1]], [[4, 5, 6, 7], [0, 0, 1]], [[0, 1, 5, 4], [0, -1, 0]], [[2, 3, 7, 6], [0, 1, 0]], [[1, 2, 6, 5], [1, 0, 0]], [[3, 0, 4, 7], [-1, 0, 0]]];
  const buf = Buffer.alloc(84 + 50 * 12);
  buf.write("cube", 0); buf.writeUInt32LE(12, 80);
  let o = 84;
  for (const [q, n] of quads) for (const t of [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]) {
    for (const v of n) { buf.writeFloatLE(v, o); o += 4; }
    for (const i of t) for (const x of c[i]) { buf.writeFloatLE(x * edge, o); o += 4; }
    o += 2;
  }
  return buf;
}

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 }, acceptDownloads: true });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  interop = await api("/interop");
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const menuItem = (name) => page.getByRole("menuitem", { name });

describe("import and export", () => {
  it("reports what this installation can convert", async () => {
    assert.ok(interop.importModel.glb.available && interop.importModel.stl.available && interop.importDrawing.svg.available);
    assert.equal(interop.exportDrawing.dwg.available, false);
    assert.ok(interop.exportDrawing.dxf.reason || interop.exportDrawing.dxf.available);
  });

  it("imports an STL cube into the model after asking its unit, and shows what the conversion assumed", async () => {
    const path = join(dataDir, "cube.stl"); writeFileSync(path, cubeStl(1000));
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Import into the model/).click();
    (await chooser).setFiles(path);
    await page.getByLabel("Length unit of the file").selectOption("mm");
    await page.getByRole("button", { name: "Import", exact: true }).click(); await settle();
    await page.getByRole("heading", { name: "Imported cube.stl" }).waitFor();
    assert.match(await page.locator("dialog").innerText(), /1 object\(s\) added/);
    assert.match(await page.locator("dialog").innerText(), /accuracy:/i);
    await page.locator("dialog .btn.primary").click();
    const objects = await scene();
    assert.equal(objects.length, 1);
    const xs = objects[0].vertices.map((v) => v[0]);
    assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - 1) < 1e-6, "a 1000 mm cube is 1 m");
  });

  it("undoes the import in one step", async () => {
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await scene()).length, 0);
    await page.keyboard.press("Control+Shift+z"); await settle();
    assert.equal((await scene()).length, 1);
  });

  it("exports the model as GLB and imports that file back", async () => {
    const [download] = await Promise.all([page.waitForEvent("download"), (async () => {
      await page.getByRole("tab", { name: "File", exact: true }).click();
      await menuItem(/Export the model/).click();
      await page.getByLabel("Format").selectOption("glb");
      await page.getByRole("button", { name: "Export", exact: true }).click();
    })()]);
    await settle();
    if (await page.locator("dialog[open]").count()) await page.locator("dialog .btn.primary").click();       // a report when the format could not carry everything
    const glb = join(dataDir, "model.glb"); await download.saveAs(glb);
    assert.equal(readFileSync(glb).subarray(0, 4).toString(), "glTF");
    await cmd("model.delete", { id: (await scene())[0].id });
    await page.reload(); await settle();                                                  // the browser learns of an outside change by reloading
    assert.equal((await scene()).length, 0);
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Import into the model/).click();
    (await chooser).setFiles(glb);
    await page.getByRole("heading", { name: "Import model.glb" }).waitFor();                // what is in the file is shown before anything changes
    assert.match(await page.locator("dialog").innerText(), /Looks like\s+GLB/);
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await page.getByRole("heading", { name: "Imported model.glb" }).waitFor();
    await page.locator("dialog .btn.primary").click();
    assert.equal((await scene()).length, 1);
  });

  it("refuses a file that is not what its name says, in words, and changes nothing", async () => {
    const bad = join(dataDir, "bad.glb"); writeFileSync(bad, "this is not a glb file");
    const revision = (await api("/bootstrap")).project.revision;
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Import into the model/).click();
    (await chooser).setFiles(bad); await settle();
    await page.getByText(/GLB|glTF|magic|invalid/i).first().waitFor();
    assert.equal(await page.getByRole("button", { name: "Import", exact: true }).isDisabled(), true, "a file that cannot be read cannot be imported");
    await page.keyboard.press("Escape"); await settle();
    assert.equal((await api("/bootstrap")).project.revision, revision);
    problems.splice(0, problems.length, ...problems.filter((p) => !/40\d/.test(p)));       // the refusal is the point of this test
  });

  it("imports a plain SVG into a plan and exports the plan as SVG again", async () => {
    const plan = (await api("/project/tree")).tree.sections[0].children[1].children[0].id;
    await cmd("draw.line", { drawing: plan, start: [0, 0], end: [2000, 0] });
    await page.reload(); await settle();
    await page.getByRole("treeitem", { name: "Level 01", exact: true }).first().click(); await settle();
    const [download] = await Promise.all([page.waitForEvent("download"), (async () => {
      await page.getByRole("tab", { name: "File", exact: true }).click();
      await menuItem(/Export the drawing/).click();
      await page.getByLabel("Format").selectOption("svg");
      await page.getByRole("button", { name: "Export", exact: true }).click();
    })()]);
    await settle();
    const svg = join(dataDir, "plan.svg"); await download.saveAs(svg);
    assert.match(readFileSync(svg, "utf8"), /<line /);
    if (await page.locator("dialog[open]").count()) {                                          // SVG cannot carry everything: the report of what it could not says so
      assert.match(await page.locator("dialog").innerText(), /Exported Level 01\.svg/);
      await page.locator("dialog .btn.primary").click(); await settle();
    }
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Import into the drawing/).click();
    (await chooser).setFiles(svg);
    await page.getByRole("heading", { name: "Import plan.svg" }).waitFor();
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await page.getByRole("heading", { name: "Imported plan.svg" }).waitFor();
    await page.locator("dialog .btn.primary").click();
    const entities = (await api(`/drawing/${plan}`)).entities.filter((e) => e.kind === "line");
    assert.equal(entities.length, 2, "the original line and the imported copy");
  });

  it("offers DXF export only where ezdxf is installed, and says what is missing otherwise", async () => {
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Export the drawing/).click();
    const option = page.getByLabel("Format").locator('option[value="dxf"]');
    assert.equal(await option.isDisabled(), !interop.exportDrawing.dxf.available);
    if (!interop.exportDrawing.dxf.available) {
      await page.getByLabel("Format").selectOption("sdrw");
      await page.getByLabel("Format").evaluate((el) => { el.value = "dxf"; el.dispatchEvent(new Event("change")); });
      assert.match(await page.locator("dialog").innerText(), /ezdxf|not available/i);
    }
    await page.keyboard.press("Escape"); await settle();
  });

  it("tells what an export will lose before anything is written", async () => {
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Export the model/).click();
    await page.getByLabel("Format").selectOption("stl");
    await page.getByRole("button", { name: "Show what will be lost" }).click();
    await page.locator("dialog .report-status").waitFor();
    assert.match(await page.locator("dialog .report-status").innerText(), /could not be carried over|Everything/);
    await page.keyboard.press("Escape"); await settle();
  });

  it("lists what has run in Operations and the failures in Diagnostics", async () => {
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Operations/).click();
    const text = await page.locator("dialog").innerText();
    assert.match(text, /Import into the model/);
    assert.match(text, /Finished/);
    await page.keyboard.press("Escape"); await settle();
    await page.getByRole("tab", { name: "File", exact: true }).click();
    await menuItem(/Diagnostics/).click();
    await page.getByRole("heading", { name: "Diagnostics" }).waitFor();
    await page.keyboard.press("Escape"); await settle();
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, [], problems.join("\n"));
  });
});
