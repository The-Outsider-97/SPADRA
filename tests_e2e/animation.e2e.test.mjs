// End-to-end tests of the animation timeline in a real browser against the real server and the real SLAI Animation Agent.
//   SPADRA_E2E_ROOT=<a SLAI root that has src/agents> node --test tests_e2e/animation.e2e.test.mjs      (build first: the server serves dist/)
// Where the agent is not importable the server reports the capability as off and these tests skip (they never fake the agent).
import { after, before, describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
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
  dataDir = mkdtempSync(join(tmpdir(), "spadra-e2e-anim-"));
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
const anim = async () => (await api("/animation")).animation;
const readout = () => page.locator(".tl-time").innerText();
const lanes = async () => (await api("/animation")).lanes;
const lane = async (oid, property) => (await lanes()).find((l) => l.object_id === oid && l.property === property);
const keyTimes = async (oid, property) => ((await lane(oid, property))?.keys ?? []).map((k) => k.time);
const field = (id, v) => page.locator(`#${id}`).fill(String(v)).then(() => page.locator(`#${id}`).press("Tab")).then(settle);
const z = async (oid, t) => { const s = await api(`/scene?time=${t}`); const w = s.objects.find((o) => o.id === oid); return w ? Math.min(...w.vertices.map((v) => v[2])) : null; };
const overlay = () => page.locator(".view-overlay").first();
const click3d = async (p) => {
  const box = await overlay().boundingBox();
  const s = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), p);
  assert.ok(s, `point ${p} is on screen`);
  await page.mouse.move(box.x + s[0], box.y + s[1], { steps: 3 });
  await page.mouse.click(box.x + s[0], box.y + s[1]); await settle();
};
/** Move the timeline's scrubber the way a person does: by changing its value (an input event). */
const scrubTo = async (t) => { await page.locator(".tl-scrub").evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event("input", { bubbles: true })); }, t); await settle(); };
const goEnd = async () => { await page.locator("#anim-end").click(); await settle(); };
const goStart = async () => { await page.locator("#anim-start").click(); await settle(); };
/** A picture of the 3D area once it has stopped changing (the scene is fetched and drawn a moment after the click). */
const pic = async () => {
  let last = (await page.locator(".canvas-area").screenshot()).toString("base64");
  for (let i = 0; i < 8; i++) { await page.waitForTimeout(250); const now = (await page.locator(".canvas-area").screenshot()).toString("base64"); if (now === last) return now; last = now; }
  return last;
};


let wallA, wallB;

before(async () => {
  url = await startServer();
  browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  page = await browser.newPage({ viewport: { width: 1672, height: 941 }, acceptDownloads: true });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()} ${m.location().url}`); });
  await page.goto(url);
  await page.getByRole("button", { name: "Create" }).click(); await settle();
  available = !!(await api("/bootstrap")).capabilities.animation;
  if (!available) return;
  const level = (await api("/project/tree")).levels[0].id;
  wallA = (await cmd("model.add_wall", { start: [0, 0], end: [4000, 0], level, thickness: 200, height: 3 })).id;
  wallB = (await cmd("model.add_wall", { start: [0, 3000], end: [4000, 3000], level, thickness: 200, height: 3 })).id;
  await page.reload(); await settle();
  await page.getByRole("treeitem", { name: "3D Model", exact: true }).first().click(); await settle();
});

after(async () => {
  await browser?.close();
  server?.kill();
  if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const ifAvailable = (fn) => async (t) => { if (!available) return t.skip("the SLAI Animation Agent is not importable in this SLAI root"); await fn(t); };

describe("making an animation", () => {
  it("starts with a plain three-step guide and no animation to set up first", ifAvailable(async () => {
    assert.equal((await api("/animation")).animation, null);
    await page.getByText("Select an element in the 3D view.").waitFor();
    await page.getByText("Change its Pose here, or drag it in the 3D view. A key is made for you.").waitFor();
    assert.equal(await readout(), "0.00 s · frame 0 / 5.00 s");
    assert.equal(await page.locator("#anim-export").isDisabled(), true, "nothing to export yet");
  }));

  it("two keys, as in the bug report: select the wall, go to the end, change its Move Z -> it rises", ifAvailable(async () => {
    await page.getByRole("button", { name: "Select", exact: true }).first().click();
    await click3d([2, 0, 1.5]);
    await page.locator("#anim-translation-z").waitFor();
    await goEnd();
    assert.equal(await readout(), "5.00 s · frame 120 / 5.00 s");
    await field("anim-translation-z", 2);
    assert.deepEqual(await lane(wallA, "translation").then((l) => l.keys), [{ time: 0, value: [0, 0, 0] }, { time: 5, value: [0, 0, 2] }], "a start key was made too");
    assert.equal(await page.locator(".tl-key").count(), 2);
    assert.equal(await page.locator(".tl-diamond.on[data-property=translation]").count(), 1, "the diamond shows this frame is keyed");
    assert.ok(Math.abs((await z(wallA, 5)) - (await z(wallA, 0)) - 2) < 1e-9);
  }));

  it("the 3D picture follows the playhead, including at time 0, and returns to the start", ifAvailable(async () => {
    await goStart(); const at0 = await pic();
    await goEnd(); const at5 = await pic();
    assert.notEqual(at0, at5);
    await goStart();
    assert.equal(await pic(), at0);
  }));

  it("moves smoothly by default (eases in and out) and linearly when asked", ifAvailable(async () => {
    const base = await z(wallA, 0);
    assert.ok(Math.abs((await z(wallA, 2.5)) - base - 1) < 1e-9, "half way at half time");
    const u = 1.25 / 5;
    assert.ok(Math.abs((await z(wallA, 1.25)) - base - 2 * (3 * u * u - 2 * u ** 3)) < 1e-9, "smoothstep at a quarter");
    await page.locator(".tl-lane-head[data-property=translation] select").selectOption("linear"); await settle();
    assert.ok(Math.abs((await z(wallA, 1.25)) - base - 0.5) < 1e-9, "linear at a quarter");
    assert.equal((await lane(wallA, "translation")).interpolation, "linear");
  }));

  it("plays to the end and stops there; Loop keeps going", ifAvailable(async () => {
    await page.locator("#anim-length, #anim-duration").first().fill("1"); await page.locator("#anim-duration").press("Tab"); await settle();
    assert.equal((await api("/animation")).animation.duration, 5, "a length shorter than the last key is refused");
    await page.locator("#anim-fps").selectOption("12"); await settle();                                  // faster to test: frame length 1/12 s
    await goStart();
    await page.locator("#anim-speed").selectOption("2");
    await page.locator("#anim-play").click();
    await page.waitForFunction(() => document.querySelector(".tl-time")?.textContent?.startsWith("5.00 s"), null, { timeout: 15000 })
      .catch(async (e) => { throw new Error(`playback did not reach 5.00 s: readout "${await page.locator(".tl-time").textContent()}", button "${await page.locator("#anim-play").textContent()}"; ${e.message}`); });
    await page.waitForFunction(() => document.querySelector("#anim-play")?.textContent === "Play");
    await page.locator("#anim-loop").check(); await settle();
    assert.equal((await api("/animation")).settings.loop, true);
    await goStart();
    await page.locator("#anim-play").click();
    await page.waitForFunction(() => { const m = /^([\d.]+) s/.exec(document.querySelector(".tl-time")?.textContent ?? ""); return m && Number(m[1]) > 2; }, null, { timeout: 15000 });
    await page.waitForFunction(() => { const m = /^([\d.]+) s/.exec(document.querySelector(".tl-time")?.textContent ?? ""); return m && Number(m[1]) < 2; }, null, { timeout: 15000 });     // wrapped round
    assert.equal(await page.locator("#anim-play").innerText(), "Pause", "still playing after the wrap");
    await page.locator("#anim-play").click();
    await page.locator("#anim-loop").uncheck(); await settle();
  }));

  it("steps by frame with the buttons and the arrow keys, and the readout names the frame", ifAvailable(async () => {
    await goStart();
    await page.locator("#anim-next").click(); await settle();
    assert.equal(await readout(), "0.08 s · frame 1 / 5.00 s");
    await page.locator(".timeline").focus();
    await page.keyboard.press("ArrowRight"); await settle();
    assert.equal(await readout(), "0.17 s · frame 2 / 5.00 s");
    await page.keyboard.press("ArrowLeft"); await page.keyboard.press("ArrowLeft"); await settle();
    assert.equal(await readout(), "0.00 s · frame 0 / 5.00 s");
    await page.keyboard.press("End"); await settle();
    assert.equal(await readout(), "5.00 s · frame 60 / 5.00 s");
  }));

  it("drags a key along its strip to move it in time, snapped to a frame", ifAvailable(async () => {
    const key = page.locator(".tl-track[data-property=translation] .tl-key").nth(1);
    const strip = await page.locator(".tl-track[data-property=translation]").boundingBox();
    const k = await key.boundingBox();
    await page.mouse.move(k.x + k.width / 2, k.y + k.height / 2);
    await page.mouse.down();
    await page.mouse.move(strip.x + strip.width * 0.8, k.y + k.height / 2, { steps: 6 });
    await page.mouse.up(); await settle();
    const times = await keyTimes(wallA, "translation");
    assert.equal(times[0], 0);
    assert.ok(Math.abs(times[1] - 4) < 1 / 12 + 1e-9, `moved to about 4 s (${times[1]})`);
    assert.ok(Math.abs(times[1] * 12 - Math.round(times[1] * 12)) < 1e-6, "on a whole frame");
  }));

  it("selects a key by clicking it and deletes it with the Delete key; undo brings it back", ifAvailable(async () => {
    await page.locator(".tl-track[data-property=translation] .tl-key").nth(1).click(); await settle();
    await page.keyboard.press("Delete"); await settle();
    assert.equal((await keyTimes(wallA, "translation")).length, 1);
    await page.keyboard.press("Control+z"); await settle();
    assert.equal((await keyTimes(wallA, "translation")).length, 2);
  }));

  it("clicking the filled diamond removes the key at this frame; clicking an empty one keys the current value", ifAvailable(async () => {
    await page.locator(".tl-key").nth(1).click(); await settle();
    const diamond = page.locator(".tl-diamond[data-property=translation]");
    assert.equal(await diamond.getAttribute("aria-pressed"), "true");
    await diamond.click(); await settle();
    assert.equal((await keyTimes(wallA, "translation")).length, 1);
    await page.locator(".tl-diamond[data-property=translation]").click(); await settle();               // empty at this frame: key what is shown
    assert.equal((await keyTimes(wallA, "translation")).length, 2);
  }));

  it("a turn and a scale are keys too, and the pose reads back what the agent evaluates", ifAvailable(async () => {
    await goEnd();
    await field("anim-turn", 270);
    await field("anim-scale-x", 2);
    assert.deepEqual((await lane(wallA, "turn")).keys.map((k) => k.value), [0, 270]);
    assert.deepEqual((await lane(wallA, "scale")).keys.map((k) => k.value), [[1, 1, 1], [2, 1, 1]]);
    assert.equal(await page.locator("#anim-turn").inputValue(), "270");
    const pose = await api(`/animation/pose?id=${wallA}&time=2.5`);
    assert.ok(pose.pose.turn > 0 && pose.pose.turn < 270);
    assert.ok(pose.pose.scale[0] > 1 && pose.pose.scale[0] < 2);
  }));

  it("shows and hides an element from a time on", ifAvailable(async () => {
    await page.getByRole("button", { name: "Select", exact: true }).first().click();
    await click3d([2, 3, 1.5]);
    await page.locator("#anim-translation-z").waitFor();
    await page.locator("#anim-time-dummy").count();
    await page.locator(".tl-ruler").evaluate(() => {});
    await page.locator("#anim-scrub, .tl-scrub").first().evaluate((el) => { el.value = "3"; el.dispatchEvent(new Event("input", { bubbles: true })); }); await settle();
    await page.locator("#anim-visible").uncheck(); await settle();
    assert.deepEqual((await lane(wallB, "visibility")).keys.map((k) => [k.time, k.value]), [[0, true], [3, false]]);
    const ids = async (t) => (await api(`/scene?time=${t}`)).objects.map((o) => o.id);
    assert.ok((await ids(2.9)).includes(wallB));
    assert.ok(!(await ids(3)).includes(wallB));
  }));

  it("applies a quick motion in one step", ifAvailable(async () => {
    await page.locator(".tl-presets summary").click();
    await page.locator("#anim-preset-kind").selectOption("spin");
    await page.locator("#anim-preset-degrees").fill("180");
    await page.locator("#anim-preset-start").fill("0");
    await page.locator("#anim-preset-end").fill("5");
    await page.locator("#anim-preset-apply").click(); await settle();
    assert.deepEqual((await lane(wallB, "turn")).keys.map((k) => [k.time, k.value]), [[0, 0], [5, 180]]);
  }));

  it("adds a marker at the playhead, jumps to it, and deletes it by double-click", ifAvailable(async () => {
    await goStart();
    await page.locator("#anim-scrub, .tl-scrub").first().evaluate((el) => { el.value = "2"; el.dispatchEvent(new Event("input", { bubbles: true })); }); await settle();
    await page.locator("#anim-marker-name").fill("Wall B turns");
    await page.locator("#anim-add-marker").click(); await settle();
    assert.deepEqual((await api("/animation")).markers.map((m) => [m.name, m.time]), [["Wall B turns", 2]]);
    await goStart();
    await page.locator(".tl-marker").click(); await settle();
    assert.match(await readout(), /^2\.00 s/);
    await page.locator(".tl-marker").dblclick(); await settle();
    assert.deepEqual((await api("/animation")).markers, []);
  }));

  it("drags the selected element in the 3D view to key where it goes (and orbits when the option is off)", ifAvailable(async () => {
    await page.getByRole("button", { name: "Select", exact: true }).first().click();
    await click3d([2, 3, 1.5]);                                                                         // wall B
    await page.locator(".tl-scrub").evaluate((el) => { el.value = "1"; el.dispatchEvent(new Event("input", { bubbles: true })); }); await settle();
    const eye = await overlay().evaluate((el) => JSON.stringify(el.spadraView.cam));
    const box = await overlay().boundingBox();
    const s = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), [2, 3, 1.5]);
    await page.mouse.move(box.x + s[0], box.y + s[1]); await page.mouse.down();
    await page.mouse.move(box.x + s[0] + 40, box.y + s[1] + 10, { steps: 5 }); await page.mouse.up(); await settle();
    assert.notEqual(await overlay().evaluate((el) => JSON.stringify(el.spadraView.cam)), eye, "orbited");
    assert.equal((await keyTimes(wallB, "translation")).length, 0, "no key without the option");
    await page.locator("#anim-drag3d").check(); await settle();
    await click3d([2, 3, 1.5]);
    const s2 = await overlay().evaluate((el, q) => el.spadraView.screenOf(q), [2, 3, 1.5]);
    await page.mouse.move(box.x + s2[0], box.y + s2[1]); await page.mouse.down();
    await page.mouse.move(box.x + s2[0] + 80, box.y + s2[1] + 5, { steps: 8 });
    const cam = await overlay().evaluate((el) => JSON.stringify(el.spadraView.cam));
    await page.mouse.up(); await settle();
    const keys = (await lane(wallB, "translation")).keys;
    assert.deepEqual(keys.map((k) => k.time), [0, 1], "a start key and a key at the playhead");
    const moved = keys[1].value;
    assert.ok(Math.hypot(moved[0], moved[1]) > 0.3, `moved on the ground (${moved})`);
    assert.equal(moved[2], 0, "and not up");
    assert.equal(await overlay().evaluate((el) => JSON.stringify(el.spadraView.cam)), cam, "the camera did not orbit");
    const pose = await api(`/animation/pose?id=${wallB}&time=1`);
    assert.ok(pose.pose.translation[0] === moved[0] && pose.keyed.includes("translation"));
    await page.locator("#anim-drag3d").uncheck(); await settle();
  }));

  it("exports every frame as a ZIP of PNG pictures that differ as the wall moves", ifAvailable(async () => {
    await page.locator("#anim-export-kind").selectOption("png");
    const wait = page.waitForEvent("download", { timeout: 120000 });
    await page.locator("#anim-export").click();
    const download = await wait;
    const file = join(dataDir, "frames.zip");
    await download.saveAs(file);
    const script = [
      "import sys, zipfile, hashlib", "z = zipfile.ZipFile(sys.argv[1])", "assert z.testzip() is None", "names = z.namelist()",
      "assert all(z.read(n)[:8] == b'\\x89PNG\\r\\n\\x1a\\n' for n in names)",
      "print(len(names), names[0], names[-1], len({hashlib.md5(z.read(n)).hexdigest() for n in names}), len(z.read(names[0])))"].join("\n");
    const [count, first, last, distinct, size] = execFileSync("python3", ["-I", "-c", script, file]).toString().trim().split(" ");
    assert.equal(Number(count), 61, "5 s at 12 frames/s, both ends included");
    assert.equal(first, "frame_0001.png");
    assert.equal(last, "frame_0061.png");
    assert.ok(Number(distinct) > 10, `frames differ (${distinct} distinct)`);
    assert.ok(Number(size) > 1000);
    assert.equal(await page.locator("#anim-export").innerText(), "Export", "the button is back to normal");
  }));

  it("exports a WebM video when the browser can record one", async (t) => {
    if (!available) return t.skip("no animation agent");
    const can = await page.evaluate(() => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported("video/webm"));
    if (!can) return t.skip("this browser cannot record WebM");
    await page.locator("#anim-fps").selectOption("12");
    await page.locator("#anim-export-kind").selectOption("webm");
    const wait = page.waitForEvent("download", { timeout: 120000 });
    await page.locator("#anim-export").click();
    const download = await wait;
    const file = join(dataDir, "movie.webm");
    await download.saveAs(file);
    const bytes = readFileSync(file);
    assert.deepEqual([...bytes.subarray(0, 4)], [0x1a, 0x45, 0xdf, 0xa3], "a Matroska / WebM header");
    assert.ok(bytes.length > 2000, `has content (${bytes.length} bytes)`);
    let ffprobe = null;
    try { ffprobe = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file]).toString().trim(); } catch { /* ffprobe is optional */ }
    if (ffprobe) assert.ok(Math.abs(Number(ffprobe) - 5) < 1.5, `duration ${ffprobe} s is about 5 s`);
  });

  it("changing the frame rate snaps new keys to it and survives save and reopen with the animation and settings", ifAvailable(async () => {
    const before = await api("/animation");
    const saved = (await api("/project/save", { method: "POST", body: JSON.stringify({ path: join(dataDir, "anim.spadra"), name: "Anim" }) })).project;
    assert.ok(saved.path && !saved.dirty);
    await api("/project/open", { method: "POST", body: JSON.stringify({ path: saved.path, discard: true }) });
    const after = await api("/animation");
    assert.deepEqual(after.lanes, before.lanes);
    assert.deepEqual(after.settings, before.settings);
    assert.equal(after.settings.fps, 12);
  }));

  it("Save As keeps the animation in the copy", ifAvailable(async () => {
    const before = await api("/animation");
    const copy = (await api("/project/save", { method: "POST", body: JSON.stringify({ name: "Anim copy", save_as: true }) })).project;
    assert.notEqual(copy.path, join(dataDir, "anim.spadra"));
    assert.deepEqual((await api("/animation")).lanes, before.lanes);
  }));

  it("raised no errors in the browser", ifAvailable(async () => {
    const real = problems.filter((p) => !/status of 40\d/.test(p));                                     // the refused length is the point of an earlier test
    assert.deepEqual(real, [], real.join("\n"));
  }));
});
