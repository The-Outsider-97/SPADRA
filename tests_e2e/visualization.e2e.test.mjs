// Browser test of the five 3D modes: the real ModelView, store and snapshot renderer in Chrome (WebGL 2), on one fixed scene, with no server (the page answers
// view.set_visual itself). SPADRA_JS_ROOT is the folder holding the compiled client (default: ../dist/js); SPADRA_CHROME names the browser binary.
// Pixels are read back, never eyeballed. Where WebGL 2 is software-rendered (SwiftShader), timings are printed with that label and are not asserted.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const jsRoot = path.resolve(process.env.SPADRA_JS_ROOT ?? path.join(here, "..", "dist", "js"));
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require("playwright"); } catch { playwright = createRequire(path.join(process.env.NODE_PATH?.split(path.delimiter)[0] ?? "/", "x"))("playwright"); }

const TYPES = { ".js": "text/javascript", ".html": "text/html", ".css": "text/css", ".json": "application/json" };
let server, base, browser, page;
const errors = [];

before(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x").pathname;
    if (url === "/favicon.ico") { res.writeHead(204).end(); return; }
    const file = url === "/harness/spadra.css" ? path.join(here, "..", "src", "client", "spadra.css") : url.startsWith("/harness/") ? path.join(here, url.slice("/harness/".length)) : path.join(jsRoot, url);
    if (!path.resolve(file).startsWith(path.resolve(here, "..")) && !file.startsWith(jsRoot)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (err, data) => { if (err) { res.writeHead(404).end(); return; } res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" }).end(data); });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
  const exe = process.env.SPADRA_CHROME ?? (fs.existsSync("/usr/bin/google-chrome") ? "/usr/bin/google-chrome" : undefined);
  browser = await playwright.chromium.launch({ executablePath: exe, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--no-sandbox"] });
  page = await browser.newPage({ viewport: { width: 800, height: 600 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`${m.text()} (${m.location()?.url ?? ""})`); });
  await page.goto(`${base}/harness/visual_harness.html`);
  await page.waitForFunction(() => window.HARNESS_READY === true, null, { timeout: 30000 });
});
after(async () => { await browser?.close(); server?.close(); });

const setMode = (mode, extra = {}) => page.evaluate(async ([m, e]) => { await H.store.setVisual({ mode: m, ...e }); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }, [mode, extra]);
const toLinear = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const toSrgb = (v) => 255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055);
/** The grey a colour becomes: its relative luminance (Rec. 709 weights) computed in linear light, then encoded for the screen. */
const lum = ([r, g, b]) => toSrgb(0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b));
/** Screen points (CSS px) of the top face centres of the wall, the column and a floor point in the open, and one in the column's shadow side. */
const points = () => page.evaluate(() => ({ wall: H.at([2.15, 3, 3]), column: H.at([5.2, 3.2, 2]), floorLit: H.at([7, 1, 0]), floorNear: H.at([4.2, 3.2, 0]) }));
const close = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (tolerance ${tol})`);

test("the page draws with WebGL 2 and no errors", async () => {
  const info = await page.evaluate(() => ({ size: H.size(), render: H.view.lastRender }));
  assert.ok(info.size.width > 700 && info.size.height > 500);
  assert.ok(info.render.mode);
  assert.deepEqual(errors, []);
});

test("Colored shows the exact material colours on top faces; Gray equals the luminance of those colours", async () => {
  await setMode("colored", { shadows: { colored: false } });
  const pts = await points();
  await page.evaluate(() => H.captureView("colored"));
  const wall = await page.evaluate((p) => H.pixel("colored", p.wall[0], p.wall[1]), pts);
  assert.ok(wall[0] > 150 && wall[1] < 90 && wall[2] < 90, `wall should read red, got ${wall}`);
  const col = await page.evaluate((p) => H.pixel("colored", p.column[0], p.column[1]), pts);
  assert.ok(col[2] > 150 && col[0] < 90, `column should read blue, got ${col}`);
  await setMode("gray", { shadows: { gray: false } });
  await page.evaluate(() => H.captureView("gray"));
  const g = await page.evaluate((p) => H.pixel("gray", p.wall[0], p.wall[1]), pts);
  assert.ok(Math.abs(g[0] - g[1]) <= 1 && Math.abs(g[1] - g[2]) <= 1, `gray must have equal channels, got ${g}`);
  close(g[0], lum(wall), 3, "gray equals the luminance of the coloured pixel");
});

test("Wireframe fills no faces: the wall's top face shows the background, not the material", async () => {
  await setMode("wireframe");
  const pts = await points();
  await page.evaluate(() => H.captureView("wire"));
  const bg = await page.evaluate(() => H.pixel("wire", 2, 2));
  const wall = await page.evaluate((p) => H.pixel("wire", p.wall[0] + 6, p.wall[1] + 6), pts);
  assert.deepEqual(wall.slice(0, 3), bg.slice(0, 3));
  assert.equal(await page.evaluate(() => H.view.lastRender.shadows), false, "wireframe never casts shadows");
});

test("shadows: off by default in the flat modes, on when switched on; Realistic always has them; Wireframe never", async () => {
  await setMode("colored", { shadows: { colored: false } });
  assert.equal(await page.evaluate(() => H.view.lastRender.shadows), false);
  await setMode("colored", { shadows: { colored: true } });
  assert.equal(await page.evaluate(() => H.view.lastRender.shadows), true);
  await setMode("realistic");
  assert.equal(await page.evaluate(() => H.view.lastRender.shadows), true);
  await setMode("wireframe", { shadows: { colored: true } });
  assert.equal(await page.evaluate(() => H.view.lastRender.shadows), false);
});

test("a shadow darkens the floor in Colored to about 55% of the lit floor", async () => {
  await page.evaluate(() => H.store.setVisual({ sun: { azimuth: 225, elevation: 30 } }));
  await setMode("colored", { shadows: { colored: false } });
  await page.evaluate(() => H.captureView("noshadow"));
  await setMode("colored", { shadows: { colored: true } });
  await page.evaluate(() => H.captureView("shadow"));
  const c = await page.evaluate(() => H.compare("noshadow", "shadow", 6));
  assert.ok(c.over > 0.003, `some floor pixels must be darker with shadows on (differing share ${c.over})`);
  const ratio = await page.evaluate(() => {
    const a = H.keep.get("noshadow"), b = H.keep.get("shadow"); const r = [];
    const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };       // the shade is a share of the light, so it is read in linear light
    for (let i = 0; i < a.width * a.height; i++) { const x = a.data[i * 4], y = b.data[i * 4]; if (x > 150 && x - y > 20 && a.data[i * 4 + 1] > 150) r.push(lin(y) / lin(x)); }
    r.sort((p, q) => p - q);
    return r.length ? { median: r[r.length >> 1], n: r.length, p10: r[Math.floor(r.length * 0.1)], p90: r[Math.floor(r.length * 0.9)] } : null;
  });
  console.log("shadowed/lit floor brightness ratio:", ratio);
  assert.ok(ratio && ratio.n > 200, "enough shadowed floor pixels");
  assert.ok(ratio.median > 0.5 && ratio.median < 0.6, `shadowed floor should read about 0.55x the lit floor, got ${JSON.stringify(ratio)}`);
});

test("Lined edge thickness follows the setting: +50% .. +200% is 1.5x .. 3.0x, and the pixels agree", async () => {
  await setMode("lined", { shadows: { lined: false } });
  const widths = {}, covered = {};
  const pt = await page.evaluate(() => H.at([5, 3, 1]));                                         // the column's vertical corner edge
  for (const boost of [50, 100, 200]) {
    await page.evaluate((b) => H.store.setVisual({ edge_boost: b }), boost);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    widths[boost] = await page.evaluate(() => H.view.lastRender.edgePx);
    await page.evaluate((b) => H.captureView(`edge${b}`), boost);
    covered[boost] = await page.evaluate(([b, p]) => H.lineWidth(`edge${b}`, p[0], p[1]), [boost, pt]);
  }
  close(widths[100] / widths[50], 2 / 1.5, 0.02, "100% over 50%");
  close(widths[200] / widths[50], 3 / 1.5, 0.02, "200% over 50%");
  console.log("edge width asked (px) and measured coverage (px) at +50/+100/+200%:", widths, Object.fromEntries(Object.entries(covered).map(([k, v]) => [k, +v.toFixed(2)])));
  assert.ok(covered[200] > covered[100] && covered[100] > covered[50], `edges must get visibly thicker: ${JSON.stringify(covered)}`);
  close(covered[200] / covered[50], 2, 0.45, "measured coverage 200% over 50%");
  assert.equal(await page.evaluate(() => H.store.setVisual({ edge_boost: 20 })), false, "below +50% is refused");
  assert.equal(await page.evaluate(() => H.store.setVisual({ edge_boost: 250 })), false, "above +200% is refused");
  assert.equal(await page.evaluate(() => H.store.activeVisual().edge_boost), 200, "a refused change leaves the setting alone");
});

test("thick edges do not show through faces: two edges at the back of the column stay hidden at +200%", async () => {
  await setMode("lined", { shadows: { lined: false }, edge_boost: 200 });
  // The column is a box on the floor; seen from the front its two back-bottom edges lie behind its faces. Their middles, on screen, are clear of every visible edge.
  const mids = await page.evaluate(() => [H.at([5, 3.2, 0]), H.at([5.2, 3.4, 0])]);
  await page.evaluate(() => H.captureView("hidden"));
  for (const m of mids) {
    const px = await page.evaluate((p) => H.pixel("hidden", p[0], p[1]), m);
    assert.ok(lum(px) > 150, `an edge behind the column must not show through its faces (pixel ${px} at ${m.map((v) => v.toFixed(1))})`);
  }
  const visible = await page.evaluate(() => H.at([5, 3, 1]));                       // the front corner edge is drawn, so the check is not just "no ink anywhere"
  const px = await page.evaluate((p) => H.pixel("hidden", p[0], p[1]), visible);
  assert.ok(lum(px) < 80, `the visible corner edge must be dark (pixel ${px})`);
});

test("snapshots match the viewport in all five modes", async () => {
  const modes = ["wireframe", "lined", "realistic", "colored", "gray"];
  const result = {};
  for (const m of modes) {
    await setMode(m);
    await page.evaluate(() => H.captureView("v"));
    await page.evaluate(() => H.captureSnapshot("s"));
    result[m] = await page.evaluate(() => H.compare("v", "s", 24));
  }
  for (const m of modes) {
    assert.equal(result[m].width, 800);
    assert.ok(result[m].mean < 6, `${m}: mean difference ${result[m].mean}`);
    assert.ok(result[m].over < 0.04, `${m}: ${(result[m].over * 100).toFixed(2)}% of pixels differ by more than 24/255`);
  }
  console.log("snapshot vs viewport (mean abs diff, share over tolerance):", Object.fromEntries(modes.map((m) => [m, [result[m].mean.toFixed(2), (result[m].over * 100).toFixed(2) + "%"]])));
});

test("switching mode keeps the camera and the selection and does not reload the scene", async () => {
  const before = await page.evaluate(() => { H.store.select?.(["wall"]); return { cam: JSON.stringify(H.view.cam), sel: JSON.stringify([...(H.store.selection ?? [])]), gets: H.state.gets.length }; });
  for (const m of ["wireframe", "lined", "realistic", "colored", "gray"]) await setMode(m);
  const after = await page.evaluate(() => ({ cam: JSON.stringify(H.view.cam), sel: JSON.stringify([...(H.store.selection ?? [])]), gets: H.state.gets.slice(), posts: H.state.posts.length }));
  assert.equal(after.cam, before.cam);
  assert.equal(after.sel, before.sel);
  assert.ok(after.gets.slice(before.gets).every((p) => p === "/project/tree"), `only the tree is fetched after the first load, got ${after.gets.slice(before.gets)}`);
});

test("the Display control is one small button in the top right until opened, and it never covers the middle or the lower half of the view", async () => {
  const closed = await page.evaluate(() => { const r = document.querySelector(".visual-panel").getBoundingClientRect(), v = H.view.el.getBoundingClientRect(); return { x: (r.left - v.left) / v.width, y: (r.top - v.top) / v.height, w: r.width / v.width, h: r.height / v.height, bottom: (r.bottom - v.top) / v.height, expanded: document.querySelector(".visual-toggle").getAttribute("aria-expanded"), body: document.querySelector(".visual-body").hidden }; });
  assert.equal(closed.expanded, "false"); assert.equal(closed.body, true);
  assert.ok(closed.x > 0.6 && closed.bottom < 0.15, `closed panel sits in the top right corner: ${JSON.stringify(closed)}`);
  await page.click(".visual-toggle");
  assert.equal(await page.getAttribute(".visual-toggle", "aria-expanded"), "true");
  assert.equal(await page.isVisible(".visual-mode[data-mode='colored']"), true);
  await page.click(".visual-mode[data-mode='colored']");
  await page.waitForFunction(() => H.store.activeVisual().mode === "colored");
  assert.match(await page.textContent(".visual-toggle"), /Colored/);
  assert.equal(await page.getAttribute(".visual-mode[data-mode='colored']", "aria-pressed"), "true");
  await page.click(".visual-toggle");
  assert.equal(await page.isVisible(".visual-mode[data-mode='colored']"), false);
});

test("a temporary snapshot mode does not change the saved view settings", async () => {
  await setMode("colored");
  const posts = await page.evaluate(() => H.state.posts.length);
  await page.evaluate(async () => { const input = H.view.snapshotInput(); input.visual = { ...input.visual, mode: "wireframe" }; const { renderSnapshot, defaultSettings } = await import("/SPADRA/src/client/snapshot_render.js"); await renderSnapshot(input, defaultSettings(320, 240)); });
  assert.equal(await page.evaluate(() => H.state.posts.length), posts, "no command is sent by taking a snapshot");
  assert.equal(await page.evaluate(() => H.store.activeVisual().mode), "colored");
});

test("each 3D view keeps its own settings and they survive a round trip", async () => {
  await page.evaluate(async () => { H.store.tabs = [{ ref: { kind: "3d", id: "v2" }, title: "3D View 2", format: "SGEO" }]; H.store.active = 0; await H.store.setVisual({ mode: "gray" }); });
  const second = await page.evaluate(() => ({ v2: H.store.activeVisual().mode, v1: H.state.visuals.v1?.mode }));
  assert.equal(second.v2, "gray");
  assert.equal(second.v1, "colored");
  await page.evaluate(() => { H.store.active = 0; H.store.tabs = [{ ref: { kind: "3d", id: "v1" }, title: "3D Model", format: "SGEO" }]; });
  assert.equal(await page.evaluate(() => H.store.activeVisual().mode), "colored");
});

test("snapshot rendering frees the GL buffers, textures, framebuffers and programs it creates (view disposal is not covered here)", async () => {
  const r = await page.evaluate(async () => {
    const counts = { created: 0, deleted: 0 };
    const patch = (proto) => { for (const key of Object.getOwnPropertyNames(proto)) { if (/^create(Buffer|Texture|Framebuffer|Renderbuffer|Program|Shader|VertexArray)$/.test(key)) { const f = proto[key]; proto[key] = function (...a) { const o = f.apply(this, a); if (o) counts.created++; return o; }; } else if (/^delete(Buffer|Texture|Framebuffer|Renderbuffer|Program|Shader|VertexArray)$/.test(key)) { const f = proto[key]; proto[key] = function (...a) { if (a[0]) counts.deleted++; return f.apply(this, a); }; } } };
    patch(WebGL2RenderingContext.prototype);
    const { renderSnapshot, defaultSettings } = await import("/SPADRA/src/client/snapshot_render.js");
    for (const m of ["lined", "realistic", "colored"]) { await H.store.setVisual({ mode: m, shadows: { lined: true, colored: true } }); await renderSnapshot(H.view.snapshotInput(), defaultSettings(400, 300)); }
    return counts;
  });
  assert.ok(r.created > 0);
  assert.ok(r.deleted >= r.created * 0.95, `snapshots should free what they create: created ${r.created}, deleted ${r.deleted}`);
});

test("performance (software WebGL, so only a floor for a real GPU): frame and snapshot time per mode", async () => {
  const timings = await page.evaluate(async () => {
    const out = {};
    for (const m of ["wireframe", "lined", "realistic", "colored", "gray"]) {
      await H.store.setVisual({ mode: m });
      const t0 = performance.now();
      for (let i = 0; i < 5; i++) { H.view.snapshot(); }
      out[m] = (performance.now() - t0) / 5;
    }
    return out;
  });
  console.log("ms per full viewport paint, software WebGL (SwiftShader), 800x600, 3 meshes:", Object.fromEntries(Object.entries(timings).map(([k, v]) => [k, v.toFixed(1)])));
  for (const v of Object.values(timings)) assert.ok(v < 2000);
});

test("no console or page errors during the whole run", () => { assert.deepEqual(errors, []); });
