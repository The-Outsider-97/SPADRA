// End-to-end tests of the SLAI Assistant, simulation, optimisation, the calculator and the agent list in a real browser against the real server.
//   SPADRA_E2E_ROOT=<a SLAI root that has src/agents> node --test tests_e2e/assistant.e2e.test.mjs      (build first: the server serves dist/)
// The assistant itself needs no agent. Agent-backed dialogs skip (never fake) where their agents are not importable.
import { after, before, beforeEach, describe, it } from "node:test";
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

let server, browser, page, url, dataDir, caps, level, wall, beam;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-asst-"));
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
const props = (id) => api("/properties", { method: "POST", body: JSON.stringify({ model: id }) });
const settle = () => page.waitForTimeout(450);
const row = async (id, key) => (await props(id)).top.concat((await props(id)).groups.flatMap((g) => g.rows)).find((r) => r.key === key)?.value;
const panel = () => page.locator(".assistant-panel");
const say = async (text) => {
  const count = await panel().locator(".ap-msg").count();
  await page.locator("#assistant-input").fill(text);
  await page.locator("#assistant-input").press("Enter");
  await page.waitForFunction((n) => document.querySelectorAll(".assistant-panel .ap-msg").length > n, count);
};
const lastMsg = () => panel().locator(".ap-msg").last();
const overlay = () => page.locator(".view-overlay").first();
const click3d = async (p) => {
  const box = await overlay().boundingBox();
  const s = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), p);
  assert.ok(s, `point ${p} is on screen`);
  await page.mouse.move(box.x + s[0], box.y + s[1], { steps: 3 });
  await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
};
const selectTool = async () => { await page.getByRole("tab", { name: "Model", exact: true }).click(); await page.getByRole("button", { name: "Select", exact: true }).first().click(); };
const pickWall = async () => { await selectTool(); await click3d([2.5, 0, 2.9]); };
const pickBeam = async () => { await selectTool(); await click3d([2.5, 3, 2.8]); };
const needs = (key) => (t) => { if (!caps[key]) { t.skip(`${key} is not available in this SLAI root`); return true; } return false; };
const analyze = () => page.getByRole("tab", { name: "Analyze", exact: true });

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/status of (400|409|503)/.test(m.text())) problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  caps = (await api("/bootstrap")).capabilities;
  level = (await api("/project/tree")).levels[0].id;
  wall = (await cmd("model.add_wall", { start: [0, 0], end: [5000, 0], level, thickness: 200, height: 3 })).id;
  beam = (await cmd("model.add_beam", { start: [0, 3000], end: [5000, 3000], level, top: 3, width: 200, depth: 300, material: "Steel" })).id;
  await page.reload(); await settle();
  await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
});

after(async () => {
  assert.deepEqual(problems, [], "no console errors or page errors");
  await browser?.close();
  if (server && server.exitCode === null) {
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 5000);
      server.once("exit", () => { clearTimeout(timer); resolve(); });
      server.kill();
    });
  }
  if (dataDir) rmSync(dataDir, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 });
});

const selectInBrowser = async (label) => { await page.locator(".browser, nav, aside").getByText(label, { exact: true }).first().click(); await settle(); };

describe("the assistant panel", () => {
  it("opens from the Analyze tab and says what it is", async () => {
    await analyze().click();
    await page.getByRole("button", { name: "Assistant", exact: true }).click();
    await panel().waitFor();
    assert.match(await panel().innerText(), /pattern interpreter, not a language model/);
    await panel().locator(".ap-chip").first().waitFor();
    assert.ok(await panel().locator(".ap-chip").count() >= 3, "real example capabilities are loaded");
  });

  it("answers an unsupported request honestly and changes nothing", async () => {
    const rev = (await api("/project/tree")).project.revision;
    await say("write me a poem");
    assert.equal(await lastMsg().getAttribute("data-kind"), "unsupported");
    assert.match(await lastMsg().innerText(), /Unsupported/i);
    assert.equal((await api("/project/tree")).project.revision, rev);
  });

  it("makes a proposal for a new wall, which waits; approving it adds the wall and verifies it", async () => {
    const before = (await api("/scene")).objects.length;
    await say("Create a wall 5 meters long, 200 mm thick, and 3 meters high from (0, 8) to (5, 8)");
    assert.equal(await lastMsg().getAttribute("data-kind"), "proposal");
    const card = lastMsg().locator(".ap-proposal");
    assert.equal(await card.getAttribute("data-status"), "pending");
    assert.match(await card.innerText(), /Waiting for your decision/i);
    assert.equal((await api("/scene")).objects.length, before);
    await card.getByRole("button", { name: "Approve" }).click();
    await page.waitForFunction(() => document.querySelector(".assistant-panel .kind-success"));
    assert.equal((await api("/scene")).objects.length, before + 1);
    assert.match(await panel().locator(".kind-success").last().innerText(), /Verified by reading the result back/);
  });

  it("changes the selected wall's height only after approval, shows before and after, and undo reverts it", async () => {
    await pickWall();
    assert.equal(await row(wall, "height"), 3);
    await say("Change the selected wall height to 3.2 meters");
    const card = lastMsg().locator(".ap-proposal");
    const text = await card.innerText();
    assert.match(text, /Height\s+3 m\s+3\.2 m/);
    assert.match(text, /Area/);
    assert.equal(await row(wall, "height"), 3);                              // still a proposal
    await card.getByRole("button", { name: "Approve" }).click();
    await page.waitForFunction(() => /Verified by reading/.test(document.querySelector(".assistant-panel")?.innerText ?? ""));
    assert.equal(await row(wall, "height"), 3.2);
    await panel().getByRole("button", { name: /^Undo/ }).last().click(); await settle();
    assert.equal(await row(wall, "height"), 3);
  });

  it("refuses to apply a proposal made for an older revision and offers to ask again", async () => {
    await say("Change the selected wall height to 4 meters");
    const card = lastMsg().locator(".ap-proposal");
    await cmd("model.set_property", { id: wall, key: "thickness", value: 250 });      // someone else changes the project
    await card.getByRole("button", { name: "Approve" }).click();
    await page.waitForFunction(() => document.querySelector(".assistant-panel .ap-proposal[data-status=stale]"));
    assert.equal(await row(wall, "height"), 3);
    assert.match(await panel().innerText(), /Out of date/i);
    assert.equal(await panel().locator(".ap-proposal[data-status=stale]").last().getByRole("button", { name: "Approve" }).count(), 0);
    await panel().getByRole("button", { name: "Ask again" }).last().click();
    await page.waitForFunction(() => document.querySelectorAll(".assistant-panel .ap-proposal[data-status=pending]").length > 0);
    await lastMsg().locator(".ap-proposal").getByRole("button", { name: "Reject" }).click();
    await page.waitForFunction(() => document.querySelector(".assistant-panel .ap-proposal[data-status=rejected]"));
    assert.equal(await row(wall, "height"), 3);
  });

  it("makes read-only answers without a proposal: explanation, calculation, check", async () => {
    const rev = (await api("/project/tree")).project.revision;
    await say("Explain the selected element's parameters");
    assert.equal(await lastMsg().getAttribute("data-kind"), "explanation");
    assert.match(await lastMsg().innerText(), /Read-only explanation/i);
    await say("Calculate the volume of the selected element");
    assert.equal(await lastMsg().getAttribute("data-kind"), "calculation");
    assert.match(await lastMsg().innerText(), /Volume: 3/);
    await say("Check whether the selected elements contain inconsistent dimensions");
    assert.equal(await lastMsg().getAttribute("data-kind"), "analysis");
    assert.equal((await api("/project/tree")).project.revision, rev);
  });

  it("treats a deletion as high risk: it needs a ticked confirmation", async () => {
    await say("Delete the selected elements");
    const card = lastMsg().locator(".ap-proposal");
    assert.match(await card.innerText(), /high risk/);
    await card.getByRole("button", { name: "Approve" }).click();
    await card.getByText("Tick the confirmation first.").waitFor();
    assert.ok((await api("/scene")).objects.some((o) => o.id === wall));
    await card.getByRole("checkbox").check();
    await card.getByRole("button", { name: "Approve" }).click();
    await page.waitForFunction(() => /deleted|Delete 1 element/.test(document.querySelector(".assistant-panel .kind-success:last-of-type")?.innerText ?? ""));
    await settle();
    assert.ok(!(await api("/scene")).objects.some((o) => o.id === wall));
    await panel().getByRole("button", { name: /^Undo/ }).last().click(); await settle();
    assert.ok((await api("/scene")).objects.some((o) => o.id === wall));
  });

  it("keeps the 2D/3D switch and the model-check toast usable with the panel open (screenshot)", async () => {
    assert.ok(await page.locator(".mode-switch").isVisible());
    await page.screenshot({ path: process.env.SPADRA_SHOT ?? join(tmpdir(), "spadra-assistant.png") });
    await page.locator(".assistant-panel .iconbtn[aria-label='Close the assistant']").click();
    assert.ok(await panel().isHidden());
  });
});

describe("agent-backed dialogs", () => {
  beforeEach(async () => { if (await page.locator("dialog[open]").count()) { await page.keyboard.press("Escape"); await settle(); } });
  it("lists every SLAI agent with the reason when it is not used", async () => {
    await analyze().click();
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await page.getByRole("heading", { name: "SLAI agents" }).waitFor();
    const text = await page.locator("dialog").innerText();
    assert.match(text, /Language Agent/i);
    assert.match(text, /Not integrated:/);
    assert.match(text, /Integrated · /);
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  });

  it("converts units and evaluates formulas with the STEM Agent", async (t) => {
    if (needs("calculator")(t)) return;
    await pickWall();
    await analyze().click();
    await page.getByRole("button", { name: "Calculator", exact: true }).click();
    await page.locator("#calc-value").fill("2.5"); await page.locator("#calc-from").fill("m"); await page.locator("#calc-to").fill("mm");
    await page.locator("#calc-convert").click();
    await page.getByText("2500 mm").waitFor();
    await page.locator("#calc-use").click();
    await page.waitForFunction(() => document.querySelector("#calc-params")?.value.includes("e1_height = 3 m"));
    await page.locator("#calc-formulas").fill("volume = e1_length * e1_height * e1_thickness");
    await page.locator("#calc-eval").click();
    const thickness = await row(wall, "thickness");
    await page.getByText(new RegExp(`= ${Number((5 * 3 * thickness / 1000).toPrecision(8))} in SI base units`)).waitFor();
    await page.locator("#calc-constraints").fill("e1_height <= 2.5 m");
    await page.locator("#calc-check").click();
    await page.getByText("At least one constraint is violated.").waitFor();
    await page.locator("#calc-convert").click();
    await page.locator("#calc-from").fill("m"); await page.locator("#calc-to").fill("kg"); await page.locator("#calc-convert").click();
    await page.getByText(/dimensionally/).waitFor();
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  });

  it("simulates the selected beam and draws the response next to its closed form", async (t) => {
    if (needs("simulation")(t)) return;
    await pickBeam();
    await analyze().click();
    await page.getByRole("button", { name: "Simulation", exact: true }).click();
    await page.locator("#sim-load").fill("10"); await page.locator("#sim-modulus").fill("200"); await page.locator("#sim-damping").fill("2");
    await page.getByRole("button", { name: "Simulate" }).click();
    await page.getByRole("heading", { name: "Response" }).waitFor({ timeout: 60000 });
    assert.ok(await page.locator("dialog svg.response-chart path").count() >= 2);
    const text = await page.locator("dialog").innerText();
    assert.match(text, /Natural frequency/);
    assert.match(text, /Agrees with the closed-form solution/);
    assert.match(text, /not a design check|not a structural design/i);
    assert.match(text, /SLAI Simulation Agent/);
    await page.locator("dialog .btn", { hasText: "Close" }).click(); await settle();
  });

  it("optimises the selected beam, then applies the result only after confirmation", async (t) => {
    if (needs("optimization")(t)) return;
    await pickBeam();
    await analyze().click();
    await page.getByRole("button", { name: "Optimization", exact: true }).click();
    await page.locator("#opt-load").fill("10"); await page.locator("#opt-modulus").fill("200");
    await page.getByRole("button", { name: "Optimise" }).click();
    await page.locator("dialog [role=alert]", { hasText: /lower and upper bound/ }).waitFor();
    await page.locator("#opt-lower").fill("100"); await page.locator("#opt-upper").fill("600");
    await page.getByRole("button", { name: "Optimise" }).click();
    await page.getByRole("heading", { name: "Evidence from the Optimization Agent" }).waitFor({ timeout: 60000 });
    const text = await page.locator("dialog").innerText();
    assert.match(text, /Smallest that meets the limit/);
    assert.match(text, /meets the limit, re-evaluated through the STEM Agent/);
    const before = await row(beam, "depth");
    assert.equal(await row(beam, "depth"), before);                         // computing changed nothing
    await page.locator("#opt-apply").click();
    await page.getByRole("button", { name: "Apply", exact: true }).click(); await settle();
    const after = await row(beam, "depth");
    assert.ok(after < before, `depth ${before} -> ${after}`);
  });
});
