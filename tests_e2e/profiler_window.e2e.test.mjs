// End-to-end test of the Profiler pop-up window and of its Plug-ins button: the real server, the real built client, a real browser.
//   npm run build            (the server serves dist/)
//   node --test tests_e2e/profiler_window.e2e.test.mjs
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
const SLAI = process.env.SPADRA_SLAI_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const VIEWPORT = { width: 1672, height: 941 };

let server, browser, page, base, dataDir;
const problems = [];

const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-pfwin-"));
  server = spawn(PYTHON, ["-m", "applications.SPADRA", "--no-browser", "--port", String(await freePort()), "--data-dir", dataDir], { cwd: SLAI, stdio: ["ignore", "pipe", "pipe"] });
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
  return (await r.json()).data;
}, [path, init]);

const settle = (ms = 350) => page.waitForTimeout(ms);
const root = () => page.locator(".pf-root");
const box = async (locator) => (await locator.boundingBox());
const near = (a, b, tolerance, what) => assert.ok(Math.abs(a - b) <= tolerance, `${what}: ${a} is not within ${tolerance} of ${b}`);
const state = () => root().getAttribute("data-state");
const menuTab = (name) => page.getByRole("tab", { name, exact: true });

/** Open Profiler from Plug-ins and start a new profile in the dialog that comes first. */
async function openProfiler() {
  await menuTab("Plug-ins").click();
  const button = page.locator(".ribbon .tool", { hasText: "Profiler" }).first();
  await button.waitFor(); await page.waitForFunction(() => { const b = [...document.querySelectorAll(".ribbon .tool")].find((x) => x.textContent?.includes("Profiler")); return b && !b.disabled; });
  await button.click();
  const dialog = page.locator("dialog[open]"); await dialog.waitFor();
  await dialog.getByRole("button", { name: "Start" }).click();
  await root().waitFor(); await settle(500);
}

/** How much a button's icon stands out from the button behind it: the share of its pixels that differ clearly from the background, and the strongest difference. */
async function inkOf(locator) {
  const icon = locator.locator("svg.glyph, img.tool-image").first();
  const b = await icon.boundingBox();
  const png = await page.screenshot({ clip: { x: b.x - 2, y: b.y - 2, width: b.width + 4, height: b.height + 4 } });
  return page.evaluate(async (data) => {
    const img = new Image(); img.src = `data:image/png;base64,${data}`; await img.decode();
    const c = document.createElement("canvas"); c.width = img.width; c.height = img.height; const g = c.getContext("2d"); g.drawImage(img, 0, 0);
    const px = g.getImageData(0, 0, c.width, c.height).data; const bg = [px[0], px[1], px[2]];
    const lum = (r, gr, bl) => 0.2126 * r + 0.7152 * gr + 0.0722 * bl; const base = lum(...bg);
    let inked = 0, peak = 0; const n = px.length / 4;
    for (let i = 0; i < px.length; i += 4) { const d = Math.abs(lum(px[i], px[i + 1], px[i + 2]) - base); if (d > 40) inked++; if (d > peak) peak = d; }
    return { share: inked / n, peak, size: [img.width, img.height] };
  }, png.toString("base64"));
}

before(async () => {
  const url = await startServer();
  base = new URL(url).origin;
  browser = await chromium.launch({ ...(process.env.SPADRA_CHROMIUM ? { executablePath: process.env.SPADRA_CHROMIUM } : {}), args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: VIEWPORT });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  page.on("response", (r) => { if (r.status() >= 400 && !r.url().includes("/api/")) problems.push(`http ${r.status()} ${r.url()}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("treeitem", { name: /^3D Model/ }).first().waitFor(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("the Profiler button in Plug-ins", () => {
  it("is an outline icon drawn like the other tool buttons, as bright and clear as they are", async () => {
    await menuTab("Insert").click();
    const wall = page.locator(".ribbon .tool", { hasText: "Wall" }).first();
    const wallInk = await inkOf(wall);
    const wallStyle = await wall.locator("svg.glyph").first().evaluate((el) => { const s = getComputedStyle(el); return { stroke: s.stroke, width: s.strokeWidth, opacity: getComputedStyle(el.closest(".tool")).opacity, w: el.getBoundingClientRect().width }; });

    await menuTab("Plug-ins").click();
    const profiler = page.locator(".ribbon .tool", { hasText: "Profiler" }).first();
    await page.waitForFunction(() => { const b = [...document.querySelectorAll(".ribbon .tool")].find((x) => x.textContent?.includes("Profiler")); return b && !b.disabled; });
    assert.equal(await profiler.locator("img").count(), 0, "no picture: a hairline-stroked PNG shrunk to 27 px is what made it dim");
    assert.equal(await profiler.locator("svg.glyph").count(), 1);
    const style = await profiler.locator("svg.glyph").first().evaluate((el) => { const s = getComputedStyle(el); return { stroke: s.stroke, width: s.strokeWidth, opacity: getComputedStyle(el.closest(".tool")).opacity, w: el.getBoundingClientRect().width }; });
    assert.equal(style.stroke, wallStyle.stroke, "same colour as the other icons");
    assert.equal(style.width, wallStyle.width, "same stroke width");
    assert.equal(style.opacity, "1"); assert.equal(style.w, wallStyle.w, "same size");

    await page.mouse.move(5, 5);                                   // no hover tint while measuring
    const ink = await inkOf(profiler);
    assert.ok(ink.peak >= wallInk.peak * 0.8, `strongest contrast ${ink.peak} against ${wallInk.peak} for Wall`);
    assert.ok(ink.share >= wallInk.share * 0.5, `ink ${(ink.share * 100).toFixed(1)} % of the icon against ${(wallInk.share * 100).toFixed(1)} % for Wall`);
    assert.ok(ink.share > 0.05, "the icon is clearly there");
  });
});

describe("the Profiler pop-up window", () => {
  it("opens as a pop-up of about 75 % of the screen, centred, with the SPADRA behind it still showing", async () => {
    await openProfiler();
    assert.equal(await root().count(), 1);
    assert.equal(await state(), "normal");
    const b = await box(root());
    near(b.width, VIEWPORT.width * 0.75, 3, "width"); near(b.height, VIEWPORT.height * 0.75, 3, "height");
    near(b.x + b.width / 2, VIEWPORT.width / 2, 3, "centred across"); near(b.y + b.height / 2, VIEWPORT.height / 2, 3, "centred down");
    assert.ok(b.width < VIEWPORT.width - 100 && b.height < VIEWPORT.height - 100, "it is not a full-screen page");
    assert.equal(await root().getAttribute("aria-modal"), "false");
    for (const name of ["Minimize Profiler", "Maximize Profiler", "Close Profiler"]) assert.equal(await root().getByRole("button", { name }).isVisible(), true, name);
    assert.equal(await root().getByRole("button", { name: "Restore Profiler" }).isVisible(), false, "nothing to restore yet");
    // the SPADRA title bar and menu are still on screen outside the window and still work while it is open
    assert.equal(await menuTab("Insert").isVisible(), true);
    await menuTab("Insert").click(); await settle(150);
    assert.equal(await menuTab("Insert").getAttribute("aria-selected"), "true", "SPADRA behind the window can be used");
    assert.equal(await root().count(), 1, "and the window stays open");
    assert.equal(await page.locator(".pf-canvas canvas").isVisible(), true);
  });

  it("minimizes to a small bar that keeps the profile, and the model behind is free to use", async () => {
    const before = await box(root());
    const drawing = await page.locator(".pf-canvas canvas").evaluate((c) => c.toDataURL());
    await root().getByRole("button", { name: "Minimize Profiler" }).click(); await settle();
    assert.equal(await state(), "min");
    const bar = await box(root());
    assert.ok(bar.height < 90 && bar.width < 560, `a small bar, not a window: ${bar.width}x${bar.height}`);
    assert.ok(bar.x >= 0 && bar.y + bar.height <= VIEWPORT.height && bar.y > VIEWPORT.height / 2, "it sits at the bottom of the screen, fully visible");
    for (const part of [".pf-ribbon", ".pf-main", ".pf-tabs", ".pf-cmdbar"]) assert.equal(await root().locator(part).first().isVisible(), false, `${part} is hidden`);
    assert.equal(await root().getByRole("button", { name: "Upload", exact: true }).isVisible(), true, "Upload stays within reach");
    assert.equal(await root().getByRole("button", { name: "Restore Profiler" }).isVisible(), true);
    assert.equal(await root().getByRole("button", { name: "Minimize Profiler" }).isVisible(), false);
    // what is at the centre of the screen now is SPADRA, not Profiler
    assert.equal(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest(".pf-root"), [VIEWPORT.width / 2, VIEWPORT.height / 2]), false);
    assert.equal(await page.evaluate(() => !!document.querySelector("dialog[open]")), false, "nothing blocks the page");
    await menuTab("Edit").click(); await settle(150);
    assert.equal(await menuTab("Edit").getAttribute("aria-selected"), "true");

    await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    assert.equal(await state(), "normal");
    const after = await box(root());
    for (const k of ["x", "y", "width", "height"]) near(after[k], before[k], 1.5, `restored ${k}`);
    assert.equal(await page.locator(".pf-canvas canvas").evaluate((c) => c.toDataURL()), drawing, "the drawing is exactly as it was");
    assert.equal(await root().count(), 1, "the same window, not a second one");
  });

  it("maximizes to the whole screen and restores to the same pop-up", async () => {
    const before = await box(root());
    await root().getByRole("button", { name: "Maximize Profiler" }).click(); await settle(500);
    assert.equal(await state(), "max");
    const b = await box(root());
    near(b.x, 0, 1, "left"); near(b.y, 0, 1, "top"); near(b.width, VIEWPORT.width, 1, "width"); near(b.height, VIEWPORT.height, 1, "height");
    assert.equal(await root().getByRole("button", { name: "Maximize Profiler" }).isVisible(), false);
    assert.equal(await root().getByRole("button", { name: "Restore Profiler" }).isVisible(), true);
    const canvas = await box(page.locator(".pf-canvas canvas"));
    assert.ok(canvas.width > before.width * 0.8, "the drawing area grew with the window");
    assert.equal(await page.locator(".pf-canvas canvas").evaluate((c) => c.width > 0 && c.height > 0), true);

    await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    assert.equal(await state(), "normal");
    const after = await box(root());
    for (const k of ["x", "y", "width", "height"]) near(after[k], before[k], 1.5, `restored ${k}`);
  });

  it("goes from maximized to minimized and back, and a double-click on the title bar toggles maximize", async () => {
    await root().getByRole("button", { name: "Maximize Profiler" }).click(); await settle();
    await root().getByRole("button", { name: "Minimize Profiler" }).click(); await settle();
    assert.equal(await state(), "min");
    await root().getByRole("button", { name: "Maximize Profiler" }).click(); await settle(500);
    assert.equal(await state(), "max", "from the bar straight to full screen");
    await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    assert.equal(await state(), "normal");
    await root().locator(".pf-title").dblclick(); await settle(500);
    assert.equal(await state(), "max");
    await root().locator(".pf-title").dblclick(); await settle(500);
    assert.equal(await state(), "normal");
  });

  it("can be moved by its title bar and stays on the screen", async () => {
    const before = await box(root());
    const grab = await box(root().locator(".pf-title"));
    const sx = grab.x + 10, sy = grab.y + grab.height / 2;
    await page.mouse.move(sx, sy); await page.mouse.down();
    await page.mouse.move(sx - 100, sy + 40, { steps: 6 }); await page.mouse.up(); await settle(200);
    const moved = await box(root());
    near(moved.x, before.x - 100, 3, "moved left"); near(moved.y, before.y + 40, 3, "moved down");
    near(moved.width, before.width, 1, "same width");
    await page.mouse.move(moved.x + 30, moved.y + 10); await page.mouse.down(); await page.mouse.move(VIEWPORT.width + 500, VIEWPORT.height + 500, { steps: 6 }); await page.mouse.up(); await settle(200);
    const pushed = await box(root());
    assert.ok(pushed.x + pushed.width <= VIEWPORT.width + 0.5 && pushed.y + pushed.height <= VIEWPORT.height + 0.5, "it cannot be dragged off the screen");
    assert.ok(pushed.x >= 0 && pushed.y >= 0);
    await root().getByRole("button", { name: "Maximize Profiler" }).click(); await settle(); await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    const back = await box(root());
    near(back.x, pushed.x, 1.5, "remembers where it was left"); near(back.y, pushed.y, 1.5, "remembers where it was left");
  });

  it("follows a browser window that gets smaller, and never ends up larger than it", async () => {
    await page.setViewportSize({ width: 1100, height: 700 }); await settle(500);
    const b = await box(root());
    assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.width <= 1100.5 && b.y + b.height <= 700.5, `${JSON.stringify(b)} fits in 1100x700`);
    await page.setViewportSize(VIEWPORT); await settle(500);
  });

  it("clicking Profiler again while it is minimized shows the same window instead of starting another", async () => {
    await root().getByRole("button", { name: "Minimize Profiler" }).click(); await settle();
    assert.equal(await state(), "min");
    await menuTab("Plug-ins").click();
    await page.locator(".ribbon .tool", { hasText: "Profiler" }).first().click(); await settle(500);
    assert.equal(await page.locator("dialog[open]").count(), 0, "no second start dialog");
    assert.equal(await root().count(), 1);
    assert.equal(await state(), "normal");
  });

  it("keeps keys apart: typing in Profiler does not drive SPADRA behind it, and keys in SPADRA do not drive Profiler", async () => {
    await menuTab("Insert").click(); await settle(150);
    const pressedBefore = await page.locator(".ribbon .tool[aria-pressed=true]").allTextContents();
    await page.locator(".pf-canvas canvas").click({ position: { x: 40, y: 40 } });
    await page.keyboard.press("w"); await settle(200);
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("pf-cmd")), true, "Profiler took the key (its command line)");
    assert.deepEqual(await page.locator(".ribbon .tool[aria-pressed=true]").allTextContents(), pressedBefore, "SPADRA's Wall shortcut did not fire");
    await page.keyboard.press("Escape");
    // a key typed in SPADRA while the window is open does not move Profiler's cursor to its command line
    await page.locator(".titlebar").click({ position: { x: 5, y: 5 } }); await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
    await page.keyboard.press("q"); await settle(200);
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("pf-cmd")), false);
  });

  it("uploads the profile to the model from the minimized bar, and the window stays", async () => {
    const countBefore = JSON.stringify(await api("/project/tree")).length;
    await root().getByRole("button", { name: "Minimize Profiler" }).click(); await settle();
    await root().getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.locator("dialog[open]"); await dialog.waitFor();
    assert.match(await dialog.locator("h2").first().textContent(), /Upload/);
    await dialog.getByRole("button", { name: "Upload", exact: true }).click();
    await page.getByText(/Uploaded: 1 object/).first().waitFor({ timeout: 15000 }); await settle(500);
    assert.notEqual(JSON.stringify(await api("/project/tree")).length, countBefore, "the model changed");
    assert.equal(await root().count(), 1, "Profiler is still open");
    assert.equal(await state(), "min", "and still a bar");
  });

  it("keeps an unsaved change through minimize and restore, then closes with the usual question", async () => {
    await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    const cmd = page.locator(".pf-cmd");
    for (const text of ["line", "0,0", "100,50"]) { await cmd.fill(text); await cmd.press("Enter"); await settle(250); }
    await cmd.press("Escape");
    await page.waitForFunction(() => document.querySelector(".pf-title .chip")?.textContent === "Unsaved");
    const drawing = await page.locator(".pf-canvas canvas").evaluate((c) => c.toDataURL());
    await root().getByRole("button", { name: "Minimize Profiler" }).click(); await settle();
    assert.equal(await root().locator(".pf-title .chip").textContent(), "Unsaved");
    await root().getByRole("button", { name: "Restore Profiler" }).click(); await settle(500);
    assert.equal(await page.locator(".pf-canvas canvas").evaluate((c) => c.toDataURL()), drawing, "same drawing after minimize and restore");
    assert.equal(await root().locator(".pf-title .chip").textContent(), "Unsaved");

    await root().getByRole("button", { name: "Close Profiler" }).click();
    const dialog = page.locator("dialog[open]"); await dialog.waitFor();
    assert.match(await dialog.textContent(), /unsaved changes/);
    await dialog.getByRole("button", { name: "Discard changes" }).click(); await settle(500);
    assert.equal(await root().count(), 0, "closed");
    // and it opens again, as a fresh pop-up
    await openProfiler();
    assert.equal(await state(), "normal");
    await root().getByRole("button", { name: "Close Profiler" }).click(); await settle(500);
    assert.equal(await root().count(), 0);
  });

  it("had no page errors", () => { assert.deepEqual(problems, []); });
});
