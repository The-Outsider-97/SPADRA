import assert from "node:assert/strict";
import { test } from "node:test";
import { cleanNumber, formatGridSize, gridLines, gridSpec, niceStep, snapPoint, snapToGrid, UNIT_MM } from "../src/client/geom2d.js";

test("automatic spacing follows the zoom and snaps to what it draws", () => {
  for (const scale of [0.01, 0.1, 1, 3.7, 25, 400]) {
    const g = gridSpec(scale);
    assert.equal(g.auto, true);
    assert.equal(g.snap, g.display);
    assert.ok(g.display * scale >= 24 - 1e-9, `lines are at least 24 px apart at scale ${scale}`);
    assert.equal(g.display, niceStep(24 / scale));
    assert.equal(g.major, cleanNumber(g.display * 5));
  }
});

test("a fixed spacing is what the pointer snaps to, and the drawn interval is a multiple of it", () => {
  const near = gridSpec(8, 10);                       // 10 mm at 8 px per mm: 80 px apart, drawn as it is
  assert.deepEqual([near.display, near.snap, near.auto, near.collapsed], [10, 10, false, false]);
  const far = gridSpec(0.5, 10);                      // 10 mm at 0.5 px per mm is 5 px: the lines are drawn every 50 mm, the pointer still snaps to 10
  assert.equal(far.snap, 10);
  assert.equal(far.display, 50);
  assert.equal(far.display % far.snap, 0);
  const tiny = gridSpec(0.1, 10);                     // 1 px: it cannot be aimed at, so snapping follows what is drawn
  assert.equal(tiny.collapsed, true);
  assert.equal(tiny.snap, tiny.display);
  assert.equal(tiny.display % 10, 0);
  for (const scale of [0.02, 0.3, 1, 7, 90]) for (const user of [0.5, 2.5, 10, 25, 1000]) {
    const g = gridSpec(scale, user);
    assert.ok(g.display * scale >= 24 - 1e-6, `${user} at ${scale}`);
    assert.ok(Math.abs(g.display / user - Math.round(g.display / user)) < 1e-9, `every drawn line is a multiple of ${user}`);
  }
});

test("invalid spacing or scale falls back to automatic instead of producing a broken grid", () => {
  for (const user of [0, -5, NaN, Infinity]) assert.equal(gridSpec(2, user).auto, true);
  assert.equal(gridSpec(0).auto, true);
  assert.equal(gridSpec(NaN).display, niceStep(24));
});

test("grid lines are indexed multiples from the origin: the axis at zero, every fifth major, negatives included", () => {
  const lines = gridLines(-23, 31, 10);
  assert.deepEqual(lines.map((l) => l.value), [-30, -20, -10, 0, 10, 20, 30, 40]);
  assert.deepEqual(lines.map((l) => l.kind), ["minor", "minor", "minor", "axis", "minor", "minor", "minor", "minor"]);
  assert.equal(gridLines(-1, 60, 10).filter((l) => l.kind === "major").map((l) => l.value).join(), "50");
  assert.equal(gridLines(-60, 1, 10).filter((l) => l.kind === "major").map((l) => l.value).join(), "-50");
  assert.equal(lines.filter((l) => l.kind === "axis").length, 1);
});

test("a long pan adds no floating-point error and a range that is too fine is refused, not drawn", () => {
  const far = gridLines(1_000_000 - 1, 1_000_000 + 1, 0.1);
  for (const l of far) assert.equal(l.value, cleanNumber(Math.round(l.value * 10) / 10), `${l.value} is a whole number of tenths`);
  assert.deepEqual(gridLines(0, 1e6, 1), []);
  assert.deepEqual(gridLines(5, 1, 1), []);
  assert.deepEqual(gridLines(0, 10, 0), []);
  const noDrift = gridLines(0, 3, 0.1).map((l) => l.value);
  assert.deepEqual(noDrift, [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3]);
});

test("snapping lands exactly on grid intersections and every drawn line is a fixed point of it", () => {
  assert.deepEqual(snapToGrid([0.31, 0.74], 0.1), [0.3, 0.7]);
  assert.deepEqual(snapToGrid([-0.04, 0.04], 0.1), [0, 0]);                      // zero, not -0
  assert.ok(Object.is(snapToGrid([-0.04, 0], 0.1)[0], 0));
  assert.deepEqual(snapToGrid([1234.4, -987.6], 10), [1230, -990]);
  assert.deepEqual(snapToGrid([3, 4], 0), [3, 4]);
  for (const step of [0.1, 2.5, 10, 250]) for (const l of gridLines(-1000, 1000, step)) assert.equal(snapToGrid([l.value, l.value], step)[0], l.value);
  const hit = snapPoint([42, 18], [], 5, 10, []);
  assert.equal(hit.kind, "grid");
  assert.deepEqual(hit.point, [40, 20]);
});

test("geometry still beats the grid and the increment is the one in force", () => {
  const wall: Array<[number, number]> = [[0, 0], [100, 0]];
  assert.equal(snapPoint([49, 2], [], 6, 10, [wall]).kind, "mid");
  assert.equal(snapPoint([49, 30], [], 6, 10, [wall]).kind, "grid");
  assert.equal(snapPoint([49, 30], [], 6, null, [wall]).kind, "none");
});

test("the status bar text gives the size in the project unit with at least one decimal", () => {
  assert.equal(formatGridSize(10, "mm"), "10.0 mm");
  assert.equal(formatGridSize(0.25, "m"), "0.25 m");
  assert.equal(formatGridSize(0.1 * 3, "m"), "0.3 m");
  assert.equal(formatGridSize(1000, "mm"), "1000.0 mm");
  assert.equal(formatGridSize(2.5, "cm"), "2.5 cm");
  assert.equal(formatGridSize(0.00012345, "m"), "0.0001 m");
});

test("one spacing in millimetres means the same length in every unit", () => {
  for (const unit of ["mm", "cm", "m"]) {
    const spacing = 250 / (UNIT_MM[unit] ?? 1);
    assert.equal(cleanNumber(spacing * (UNIT_MM[unit] ?? 1)), 250);
  }
  assert.deepEqual(Object.keys(UNIT_MM).sort(), ["cm", "m", "mm"]);
});
