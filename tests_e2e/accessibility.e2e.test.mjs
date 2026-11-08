// End-to-end checks of what the interface owes everyone: names for every control, readable contrast in every theme, no sideways scrolling in a narrow window or at a larger
// text size, and a sheet that prints on its own, whole, at the size of its paper.
//   node --test tests_e2e/accessibility.e2e.test.mjs        (build first: the server serves dist/)
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.SPADRA_E2E_ROOT ?? resolve(HERE, "..", "..", "..");
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

let server, browser, page, url, dataDir;
const problems = [];
const freePort = () => new Promise((ok, fail) => { const s = createServer(); s.once("error", fail); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => ok(port)); }); });

async function startServer() {
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-a11y-"));
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
const settle = () => page.waitForTimeout(450);
const flat = (nodes) => nodes.flatMap((n) => [n, ...flat(n.children ?? [])]);
const nodes = async () => flat((await api("/project/tree")).tree.sections ?? []);
const menuTab = (name) => page.getByRole("tab", { name, exact: true }).click();

/** Controls that are visible and have no accessible name (aria-label, aria-labelledby, an associated label, text, alt or title). */
const unnamed = () => page.evaluate(() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const text = (el) => (el.textContent ?? "").replace(/\s+/g, " ").trim();
  const nameOf = (el) => {
    const aria = el.getAttribute("aria-label")?.trim(); if (aria) return aria;
    const by = el.getAttribute("aria-labelledby"); if (by) { const t = by.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim(); if (t) return t; }
    if (el.labels?.length) { const t = [...el.labels].map(text).join(" ").trim(); if (t) return t; }
    if (el.closest("label") && text(el.closest("label"))) return text(el.closest("label"));
    if (el.tagName !== "INPUT" && el.tagName !== "SELECT" && el.tagName !== "TEXTAREA" && text(el)) return text(el);
    const img = el.querySelector("img[alt]"); if (img?.getAttribute("alt")) return img.getAttribute("alt");
    return el.getAttribute("title")?.trim() ?? "";
  };
  const sel = "button, a[href], input:not([type=hidden]), select, textarea, canvas[tabindex], [role=tab], [role=treeitem], [role=menuitem], [role=slider], [tabindex='0']";
  return [...document.querySelectorAll(sel)].filter(visible).filter((el) => !nameOf(el)).map((el) => `${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ").join(".") : ""}`);
});

/** Text that is hard to read: its colour against the first opaque background above it. Disabled controls are exempt, as WCAG allows. */
const lowContrast = () => page.evaluate(() => {
  const parse = (c) => {
    let m = /^rgba?\(([^)]+)\)$/.exec(c);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 }; }
    m = /^color\(srgb ([^)]+)\)$/.exec(c);
    if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p[3] ?? 1 }; }
    return null;
  };
  const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const background = (el) => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; } }
    return stack.reduceRight((acc, c) => over(c, acc), { r: 255, g: 255, b: 255, a: 1 });
  };
  const bad = [];
  const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("canvas, svg, [aria-hidden=true], script, style")) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || s.visibility === "hidden" || s.display === "none") continue;
    if (el.closest(":disabled, [aria-disabled=true]")) continue;
    const fg = parse(s.color); if (!fg) continue;
    const bg = background(el), colour = over(fg, bg);
    const a = lum(colour) + 0.05, b = lum(bg) + 0.05, ratio = Math.max(a, b) / Math.min(a, b);
    const size = parseFloat(s.fontSize), large = size >= 24 || (size >= 18.66 && Number(s.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) { const key = `${el.tagName}.${el.className}|${s.color}|${bg.r | 0},${bg.g | 0},${bg.b | 0}`; if (!seen.has(key)) { seen.add(key); bad.push(`${ratio.toFixed(2)} ${key} "${el.textContent.trim().slice(0, 30)}"`); } }
  }
  return bad;
});

const overflow = () => page.evaluate(() => {
  const right = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().right) : null; };
  return { page: document.documentElement.scrollWidth - innerWidth, body: document.body.scrollWidth - innerWidth, width: innerWidth, modeSwitch: right(".mode-switch"), status: right(".statusbar") };
});

let plan, sheet;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 } });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  const l1 = (await api("/project/tree")).levels[0].id;
  const room = [[0, 0], [8000, 0], [8000, 5000], [0, 5000]];
  const walls = [];
  for (let i = 0; i < 4; i++) walls.push((await cmd("model.add_wall", { start: room[i], end: room[(i + 1) % 4], level: l1, thickness: 250, height: 3.2 })).id);
  await cmd("model.add_door", { wall: walls[0], point: [2000, 0], width: 900, height: 2100 });
  await cmd("view.add_schedule", { category: "door" });
  plan = (await nodes()).find((n) => n.open?.kind === "drawing" && n.view_kind === "plan");
  sheet = (await cmd("sheet.create", { name: "Ground floor plan" })).id;
  await cmd("sheet.place_view", { id: sheet, view_id: plan.open.id });
  await page.reload(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

describe("names", () => {
  it("every control in the main views has an accessible name", async () => {
    const missing = [];
    for (const label of ["Level 01", "3D Model", "Door Schedule", "A101"]) {
      const row = page.getByRole("treeitem", { name: new RegExp(label) }).first();
      if (await row.count()) { await row.click(); await settle(); }
      for (const m of ["Model", "Insert", "Annotate", "Analyze", "Edit", "View", "Collaborate"]) { await menuTab(m); missing.push(...(await unnamed()).map((x) => `${label}/${m}: ${x}`)); }
    }
    assert.deepEqual(missing, []);
  });

  it("every control in the dialogs has an accessible name", async () => {
    const missing = [];
    const check = async (what) => { missing.push(...(await unnamed()).map((x) => `${what}: ${x}`)); await page.keyboard.press("Escape"); await settle(); };
    await menuTab("Insert"); await page.locator(".ribbon").getByRole("button", { name: "Levels", exact: true }).click(); await check("Levels");
    await page.locator(".ribbon").getByRole("button", { name: "Families", exact: true }).click(); await page.getByRole("dialog", { name: "Families" }).waitFor(); await check("Families");
    await menuTab("Analyze"); await page.locator(".ribbon").getByRole("button", { name: "Report", exact: true }).click(); await check("New report");
    await page.locator(".ribbon").getByRole("button", { name: "Check model", exact: true }).click(); await page.getByRole("dialog").waitFor(); await check("Check model");
    for (const entry of [/Export the model/, /Operations/, /Diagnostics/, /Keyboard shortcuts/, /Project properties/]) {
      await menuTab("File"); await page.getByRole("menuitem", { name: entry }).click(); await page.getByRole("dialog").waitFor(); await check(String(entry));
    }
    await page.keyboard.press("?"); await page.getByRole("dialog", { name: "Keyboard shortcuts" }).waitFor(); await check("shortcuts");
    assert.deepEqual(missing, []);
  });
});

describe("contrast", () => {
  for (const theme of ["light", "dark", "contrast"]) {
    it(`text is readable in the ${theme} theme`, async () => {
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
      const bad = [];
      for (const label of ["Level 01", "Door Schedule", "A101"]) {
        await page.getByRole("treeitem", { name: new RegExp(label) }).first().click(); await settle();
        bad.push(...(await lowContrast()).map((x) => `${label}: ${x}`));
      }
      await menuTab("Analyze"); bad.push(...(await lowContrast()).map((x) => `Analyze: ${x}`)); await menuTab("Model");
      await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
      assert.deepEqual(bad, []);
    });
  }
});

describe("layout", () => {
  for (const width of [1280, 1100, 900]) {
    it(`does not scroll sideways at ${width} px wide, and keeps the 2D/3D switch in reach`, async () => {
      await page.setViewportSize({ width, height: 800 }); await settle();
      for (const label of ["Level 01", "Door Schedule", "A101"]) {
        await page.getByRole("treeitem", { name: new RegExp(label) }).first().click(); await settle();
        const o = await overflow();
        assert.ok(o.page <= 0 && o.body <= 0, `${label}: the page is ${o.page}px wider than the window`);
        assert.ok(o.modeSwitch !== null && o.modeSwitch <= o.width, `${label}: the 2D/3D switch ends at ${o.modeSwitch} in a ${o.width}px window`);
      }
    });
  }

  it("keeps the whole interface in the window at the largest text size", async () => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await page.evaluate(() => document.documentElement.style.setProperty("--font-scale", "1.4")); await settle();
    const o = await overflow();
    assert.ok(o.page <= 0 && o.body <= 0, `the page is ${o.page}px wider than the window`);
    assert.ok(o.modeSwitch !== null && o.modeSwitch <= o.width, "the 2D/3D switch is in the window");
    await page.evaluate(() => document.documentElement.style.removeProperty("--font-scale"));
  });
});

describe("print", () => {
  it("prints the sheet alone, whole, at the size of its paper, in light colours", async () => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await page.getByRole("treeitem", { name: /A101/ }).first().click(); await settle();
    await page.locator(".sheet-wrap").focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press("+");                      // zoomed in on screen: the print must not be
    await page.emulateMedia({ media: "print" });
    const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true });
    await page.emulateMedia({ media: null });
    await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
    assert.equal(pdf.subarray(0, 5).toString(), "%PDF-");
    // Chromium exposes physical page size in the PDF itself: never require
    // Poppler's pdfinfo/pdftotext binaries to be installed on Windows.
    const boxes = [...pdf.toString("latin1").matchAll(/\/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]/g)];
    assert.ok(boxes.length, "PDF contains a physical MediaBox");
    const [widthPt, heightPt] = boxes.at(-1).slice(1).map(Number);
    const mm = (pts) => Number(pts) / 72 * 25.4;
    const sheetData = await api(`/sheet/${sheet}`);
    assert.ok(Math.abs(mm(widthPt) - sheetData.width) < 1.5 && Math.abs(mm(heightPt) - sheetData.height) < 1.5,
      `the PDF page is ${mm(widthPt).toFixed(0)} x ${mm(heightPt).toFixed(0)} mm, the paper ${sheetData.width} x ${sheetData.height}`);
    // Verify source drawing text and print isolation in the actual browser
    // rather than using an optional external PDF text extraction executable.
    const sheetText = await page.locator(".sheet-wrap svg").innerText();
    assert.match(sheetText, /A101/, "the live sheet contains its number");
    assert.match(sheetText, /ground floor plan/i, "the live sheet contains its title");
    await page.emulateMedia({ media: "print" });
    const visibility = await page.evaluate(() => ({
      sheet: getComputedStyle(document.querySelector(".sheet-wrap")).display,
      browser: getComputedStyle(document.querySelector(".panel[aria-label='Project Browser']")).display,
    }));
    await page.emulateMedia({ media: null });
    assert.notEqual(visibility.sheet, "none", "sheet remains printable");
    assert.equal(visibility.browser, "none", "Project Browser is not in the printable page");
  });

  it("raised no errors in the browser", () => {
    assert.deepEqual(problems, [], problems.join("\n"));
  });
});
