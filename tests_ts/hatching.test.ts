import assert from "node:assert/strict";
import { test } from "node:test";
import { cutStyle, hatchFill, hatchPath, patternPath } from "../src/client/projection.js";
import type { HatchSpec } from "../src/client/types.js";

const square: Array<[number, number]> = [[0, 0], [10, 0], [10, 10], [0, 10]];

test("each material of a build-up is hatched in its own way", () => {
  assert.equal(cutStyle("Concrete").hatch, "diag");
  assert.equal(cutStyle("EPS").hatch, "cross");
  assert.equal(cutStyle("Mineral wool").hatch, "cross");
  assert.equal(cutStyle("Sand").hatch, "dots");
  assert.equal(cutStyle("Screed").hatch, "none");
  assert.ok(cutStyle("EPS").alpha < cutStyle("Concrete").alpha, "insulation is drawn lighter than concrete");
});

test("diagonal hatching stays inside the loop and is evenly spaced", () => {
  const d = hatchPath(square, "diag", 2);
  const segs = [...d.matchAll(/M(-?[\d.]+) (-?[\d.]+)L(-?[\d.]+) (-?[\d.]+)/g)].map((m) => m.slice(1).map(Number) as [number, number, number, number]);
  assert.ok(segs.length >= 8 && segs.length <= 11, `about ten lines across a 10 x 10 square at 2 spacing, got ${segs.length}`);
  for (const [x0, y0, x1, y1] of segs) {
    for (const v of [x0, y0, x1, y1]) assert.ok(v >= -1e-9 && v <= 10 + 1e-9, "inside the square");
    assert.ok(Math.abs(Math.abs(x1 - x0) - Math.abs(y1 - y0)) < 1e-2, "at 45 degrees");
  }
});

test("cross hatching has twice the lines of diagonal hatching", () => {
  const count = (s: string): number => (s.match(/M/g) ?? []).length;
  assert.equal(count(hatchPath(square, "cross", 2)), 2 * count(hatchPath(square, "diag", 2)));
});

test("a concave loop is hatched only where it is solid", () => {
  const ell: Array<[number, number]> = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]];
  const segs = [...hatchPath(ell, "diag", 1).matchAll(/M(-?[\d.]+) (-?[\d.]+)L(-?[\d.]+) (-?[\d.]+)/g)].map((m) => m.slice(1).map(Number) as [number, number, number, number]);
  for (const [x0, y0, x1, y1] of segs) {
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    assert.ok(!(mx > 4 + 1e-6 && my > 4 + 1e-6), "no hatching in the notch");
  }
});

test("dots fall only inside the loop, and nothing is drawn for a degenerate loop", () => {
  const pts = [...hatchPath(square, "dots", 2).matchAll(/M(-?[\d.]+) (-?[\d.]+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
  assert.ok(pts.length > 10);
  for (const [x, y] of pts) assert.ok(x! > 0 && x! < 10 && y! > 0 && y! < 10);
  assert.equal(hatchPath([[0, 0], [1, 1]], "diag", 1), "");
  assert.equal(hatchPath(square, "diag", 0), "");
});

const spec = (kind: HatchSpec["kind"], angle = 45): HatchSpec => ({ pattern: `${kind}-${angle}`, label: kind, kind, angle, spacing_mm: 2, standard: null });
const segments = (d: string): Array<[number, number, number, number]> => [...d.matchAll(/M(-?[\d.]+) (-?[\d.]+)L(-?[\d.]+) (-?[\d.]+)/g)].map((m) => m.slice(1).map(Number) as [number, number, number, number]);

test("a hatch has lines at its own angle, evenly spaced, inside the loop", () => {
  for (const angle of [0, 45, 90, 135, 30]) {
    const segs = segments(patternPath([square], spec("lines", angle), 2));
    assert.ok(segs.length >= 4, `lines at ${angle}`);
    for (const [x0, y0, x1, y1] of segs) {
      for (const v of [x0, y0, x1, y1]) assert.ok(v >= -1e-3 && v <= 10 + 1e-3, "inside the square");
      const turn = (((Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI) % 180 + 180) % 180;
      assert.ok(Math.abs(turn - angle) < 0.5 || Math.abs(turn - angle - 180) < 0.5 || Math.abs(turn + 180 - angle) < 0.5, `${turn} is not ${angle}`);
    }
  }
});

test("crossed lines are two sets, one a quarter turn from the other", () => {
  const segs = segments(patternPath([square], spec("cross", 0), 2));
  const horizontal = segs.filter(([, y0, , y1]) => Math.abs(y1 - y0) < 1e-3).length, vertical = segs.filter(([x0, , x1]) => Math.abs(x1 - x0) < 1e-3).length;
  assert.equal(horizontal + vertical, segs.length, "every line is along one of the two directions");
  assert.ok(horizontal >= 4 && vertical >= 4, `${horizontal} horizontal, ${vertical} vertical`);
});

test("a hole in the cut stays clear of hatching", () => {
  const hole: Array<[number, number]> = [[3, 3], [7, 3], [7, 7], [3, 7]];
  for (const [x0, y0, x1, y1] of segments(patternPath([square, hole], spec("lines", 0), 1))) {
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    assert.ok(!(mx > 3 + 1e-6 && mx < 7 - 1e-6 && my > 3 + 1e-6 && my < 7 - 1e-6), "nothing inside the hole");
  }
});

test("none and solid draw no lines, and a solid pattern is the only one that fills", () => {
  assert.equal(patternPath([square], spec("none"), 2), "");
  assert.equal(patternPath([square], spec("solid"), 2), "");
  assert.equal(hatchFill(spec("solid")), 1);
  assert.equal(hatchFill(spec("lines")), 0, "hatched cuts show paper between the lines");
  assert.equal(hatchFill(undefined), 1, "no hatch: the plain cut as before");
});

test("on a screen with y down the angle is mirrored so the pattern looks the same", () => {
  const up = segments(patternPath([square], spec("lines", 45), 2, false))[0] as [number, number, number, number];
  const down = segments(patternPath([square], spec("lines", 45), 2, true))[0] as [number, number, number, number];
  assert.ok(Math.sign((up[3] - up[1]) * (up[2] - up[0])) === -Math.sign((down[3] - down[1]) * (down[2] - down[0])), "slopes have opposite signs");
});

test("dots stay inside the loop", () => {
  const d = patternPath([square], spec("dots"), 2);
  for (const m of d.matchAll(/M(-?[\d.]+) (-?[\d.]+)l/g)) { const x = Number(m[1]), y = Number(m[2]); assert.ok(x > 0 && x < 10 && y > 0 && y < 10); }
  assert.ok(d.length > 0);
});
