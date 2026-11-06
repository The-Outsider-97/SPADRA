import assert from "node:assert/strict";
import { test } from "node:test";
import { snapPoint } from "../src/client/geom2d.js";

const gridA: Array<[number, number]> = [[0, 0], [10000, 0]];
const gridB: Array<[number, number]> = [[4000, -2000], [4000, 8000]];

test("two grid lines snap at their crossing, as at their ends and middles", () => {
  const hit = snapPoint([4010, 12], [], 40, null, [gridA, gridB]);
  assert.equal(hit.kind, "intersection");
  assert.deepEqual(hit.point, [4000, 0]);
  const end = snapPoint([9990, 5], [], 40, null, [gridA, gridB]);
  assert.equal(end.kind, "end");
  assert.deepEqual(end.point, [10000, 0]);
  const mid = snapPoint([5000, 8], [], 40, null, [gridA, gridB]);
  assert.equal(mid.kind, "mid");
});

test("nothing snaps to a grid line the pointer is not near", () => {
  assert.equal(snapPoint([2000, 900], [], 40, null, [gridA, gridB]).kind, "none");
});

test("a wall's outline is not treated as a line to cross", () => {
  const wall: Array<[number, number]> = [[0, -100], [10000, -100], [10000, 100], [0, 100]];
  assert.notEqual(snapPoint([4000, 50], [], 40, null, [wall, gridB]).kind, "intersection");
});
