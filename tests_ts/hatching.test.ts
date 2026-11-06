import assert from "node:assert/strict";
import { test } from "node:test";
import { cutStyle, hatchPath } from "../src/client/projection.js";

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
