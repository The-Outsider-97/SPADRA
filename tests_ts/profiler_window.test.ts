import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { clampGeometry, defaultGeometry, MIN_WINDOW, toggledState, WINDOW_FRACTION } from "../src/client/profiler_window.js";

/** Run from the SPADRA folder, as npm test does: the sources are read as text so the checks need no browser. */
const read = (path: string): string => readFileSync(path, "utf8");
const ICONS = read("src/client/icons.ts"), SHELL = read("src/client/shell.ts"), UI = read("src/client/profiler_ui.ts"), CSS = read("src/client/spadra.css"), CANVAS = read("src/client/profiler_canvas.ts");

// ------------------------------------------------------------------------------------------------ window geometry
test("a new Profiler window takes three quarters of the screen each way and is centred", () => {
  assert.equal(WINDOW_FRACTION, 0.75);
  for (const [w, h] of [[1920, 1080], [1366, 768], [2560, 1440], [1280, 720], [800, 600]] as const) {
    const g = defaultGeometry({ w, h });
    assert.equal(g.w, Math.round(w * 0.75)); assert.equal(g.h, Math.round(h * 0.75));
    assert.ok(Math.abs(g.x - (w - g.w) / 2) <= 0.5 && Math.abs(g.y - (h - g.h) / 2) <= 0.5, `${w}x${h} is centred`);
    assert.ok(g.x >= 0 && g.y >= 0 && g.x + g.w <= w && g.y + g.h <= h, `${w}x${h} stays on screen`);
  }
});

test("a window can never be larger than the screen, nor so small that its ribbon is cut off", () => {
  const vp = { w: 1600, h: 900 };
  assert.deepEqual(clampGeometry({ x: -50, y: -50, w: 5000, h: 5000 }, vp), { x: 0, y: 0, w: 1600, h: 900 });
  assert.deepEqual(clampGeometry({ x: 10, y: 10, w: 100, h: 100 }, vp), { x: 10, y: 10, w: MIN_WINDOW.w, h: MIN_WINDOW.h });
  const small = clampGeometry({ x: 0, y: 0, w: 100, h: 100 }, { w: 500, h: 400 });
  assert.equal(small.w, 500); assert.equal(small.h, 400);                      // the screen itself is smaller than the minimum: the window fills it, never overflows it
});

test("a window dragged or left behind by a smaller screen is pulled back into view, and clamping changes nothing that already fits", () => {
  const vp = { w: 1600, h: 900 };
  const moved = clampGeometry({ x: 1500, y: 880, w: 1200, h: 675 }, vp);
  assert.deepEqual(moved, { x: 400, y: 225, w: 1200, h: 675 });
  assert.ok(moved.x + moved.w <= vp.w && moved.y + moved.h <= vp.h);
  const fits = { x: 200, y: 100, w: 1200, h: 675 };
  assert.deepEqual(clampGeometry(fits, vp), fits);
  assert.deepEqual(clampGeometry(clampGeometry(moved, vp), vp), moved);        // idempotent
  const shrunk = clampGeometry(defaultGeometry({ w: 1920, h: 1080 }), { w: 1000, h: 700 });
  assert.ok(shrunk.x >= 0 && shrunk.y >= 0 && shrunk.x + shrunk.w <= 1000 && shrunk.y + shrunk.h <= 700);
});

test("double-clicking the title bar maximises a normal window and restores any other", () => {
  assert.equal(toggledState("normal"), "max"); assert.equal(toggledState("max"), "normal"); assert.equal(toggledState("min"), "normal");
});

// ------------------------------------------------------------------------------------------------ the icon
const profilerGlyph = (): string[] => {
  const m = /"pf-profiler":\s*\[((?:"[^"]*",?\s*)+)\]/.exec(ICONS); assert.ok(m, "icons.ts registers the glyph pf-profiler");
  return [...m![1]!.matchAll(/"([^"]*)"/g)].map((x) => x[1]!);
};

test("the Profiler plug-in button uses an outline glyph like every other tool button, not the 1440 px picture", () => {
  const line = SHELL.split("\n").find((l) => l.includes('if (id === "profiler")'));
  assert.ok(line, "the Plug-ins menu has a Profiler entry");
  assert.ok(!/image:/.test(line!), "no <img> with hairline strokes");
  assert.match(SHELL, /profiler: "pf-profiler"/);
});

test("the pf-profiler glyph is the supplied artwork on the 24 px grid: one 5-point line and 5 vertex dots, with room for the stroke", () => {
  const paths = profilerGlyph();
  assert.equal(paths.length, 6);
  const nums = paths[0]!.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  assert.equal(nums.length, 10, "five points");
  for (const n of nums) assert.ok(n >= 2 && n <= 22, `${n} leaves room for the 1.6 px stroke inside the 24 px grid`);
  const pts: Array<[number, number]> = []; for (let i = 0; i < nums.length; i += 2) pts.push([nums[i]!, nums[i + 1]!]);
  // the artwork: A (23,1120)  B (1077,25)  C (1415,657)  D (760,963)  E (904,488) on a 1440 px square; B is the highest point, A the lowest and leftmost, C the rightmost
  const [A, B, C, D, E] = pts as [[number, number], [number, number], [number, number], [number, number], [number, number]];
  assert.ok(B[1] < E[1] && B[1] < C[1] && A[1] > D[1] && A[0] < D[0] && C[0] > B[0] && E[1] < D[1], "the points keep the shape of the supplied picture");
  for (const dot of paths.slice(1)) assert.match(dot, /^M[\d. -]+a[\d.]+ [\d.]+ 0 1 0 [\d.-]+ 0a/, "a dot is a small closed circle");
});

test("the window and Profiler glyphs are all registered", () => {
  for (const name of ["pf-profiler", "pf-minimize", "pf-maximize", "pf-restore"]) assert.ok(new RegExp(`"${name}":\\s*\\[`).test(ICONS), `${name} is registered`);
  for (const name of ["pf-profiler", "pf-minimize", "pf-maximize", "pf-restore"]) assert.ok(UI.includes(`glyph("${name}")`), `${name} is used by the window`);
});

// ------------------------------------------------------------------------------------------------ the window
test("Profiler is a pop-up that does not block SPADRA, with minimize, maximize, restore and close", () => {
  assert.match(UI, /"aria-modal": "false"/); assert.ok(!/"aria-modal": "true"/.test(UI));
  for (const label of ["Minimize Profiler", "Maximize Profiler", "Restore Profiler", "Close Profiler"]) assert.ok(UI.includes(`"aria-label": "${label}"`), label);
  for (const state of ["normal", "max", "min"]) assert.ok(CSS.includes(`.pf-root[data-state="${state}"]`) || state === "normal", `style for ${state}`);
  assert.match(CSS, /\.pf-root\s*\{[^}]*width: 75vw[^}]*height: 75vh/);
  assert.match(CSS, /\.pf-root\[data-state="max"\][^}]*100vw[^}]*100vh/);
  assert.match(CSS, /\.pf-root\[data-state="min"\] > :not\(\.pf-header\)\s*\{ display: none/);
});

test("a window that is not modal only reacts to keys typed inside it, and SPADRA ignores keys typed in it", () => {
  assert.match(UI, /this\.root\.contains\(e\.target\)/);
  assert.match(UI, /this\.state === "min"/);
  assert.match(SHELL, /closest\("\.pf-root"\)/);
});

test("minimising never destroys the drawing: a hidden canvas keeps its size and view, and a second click on Profiler brings the window back", () => {
  assert.match(CANVAS, /r\.width < 2 \|\| r\.height < 2\) return/);
  assert.match(UI, /open\.bringBack\(\)/);
  assert.match(UI, /private static starting/);
});
