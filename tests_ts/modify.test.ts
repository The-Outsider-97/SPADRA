import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { arcPoints, entityBounds, entityDistance, gripAt, grips, hatchContains, hatchLoops, snapPoint } from "../src/client/geom2d.js";
import type { Entity } from "../src/client/types.js";

const arc: Entity = { id: "a", kind: "arc", layer: "0", geometry: { center: [0, 0], radius: 10, start_angle: 0, end_angle: Math.PI / 2 } };
const rect = (id: string, x: number, y: number, w: number, h: number): Entity => ({ id, kind: "polyline", layer: "0", geometry: { points: [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], closed: true } });
const hatchOf = (loops: Array<Record<string, unknown>>): Entity => ({ id: "h", kind: "hatch", layer: "0", geometry: { boundary: loops, pattern: "solid" } });

describe("arcs", () => {
  it("samples from the start angle to the end angle counter-clockwise", () => {
    const pts = arcPoints(arc.geometry);
    assert.deepEqual(pts[0]?.map(Math.round), [10, 0]);
    const last = pts[pts.length - 1]!;
    assert.ok(Math.abs(last[0]) < 1e-9 && Math.abs(last[1] - 10) < 1e-9);
    assert.ok(pts.every((p) => Math.abs(Math.hypot(p[0], p[1]) - 10) < 1e-9));
  });
  it("wraps an end angle below the start angle round a full turn", () => {
    const pts = arcPoints({ center: [0, 0], radius: 1, start_angle: 3 * Math.PI / 2, end_angle: Math.PI / 2 });
    const last = pts[pts.length - 1]!;
    assert.ok(Math.abs(last[1] - 1) < 1e-9);
    assert.ok(pts.some((p) => p[0] > 0.99), "passes through angle 0 on the way");
  });
  it("is hit near the arc but not on the missing part of the circle", () => {
    assert.ok(entityDistance(arc, [7.07, 7.07]) < 0.1);
    assert.ok(entityDistance(arc, [-10, 0]) > 10);
  });
  it("has bounds of its own sweep, and snaps to its ends and centre", () => {
    const b = entityBounds(arc)!;
    assert.ok(Math.abs(b[0][0]) < 1e-9 && Math.abs(b[1][0] - 10) < 1e-9 && Math.abs(b[1][1] - 10) < 1e-9);
    assert.equal(snapPoint([10.2, 0.1], [arc], 1, null).kind, "end");
    assert.equal(snapPoint([0.1, 0.1], [arc], 1, null).kind, "center");
  });
});

describe("hatches", () => {
  const outer = { kind: "polygon", points: [[0, 0], [10, 0], [10, 10], [0, 10]] };
  const hole = { kind: "polygon", points: [[4, 4], [6, 4], [6, 6], [4, 6]] };
  it("contains points by the even-odd rule, leaving holes open", () => {
    const h = hatchOf([outer, hole]);
    assert.equal(hatchContains(h, [1, 1]), true);
    assert.equal(hatchContains(h, [5, 5]), false);
    assert.equal(hatchContains(h, [11, 5]), false);
  });
  it("reads circle outlines and is picked only by its fill", () => {
    const h = hatchOf([{ kind: "circle", center: [0, 0], radius: 5 }]);
    assert.equal(hatchLoops(h)[0]?.length, 64);
    assert.equal(entityDistance(h, [1, 1]), 0);
    assert.equal(entityDistance(h, [9, 0]), Infinity);
  });
  it("has the bounds of its outline", () => {
    assert.deepEqual(entityBounds(hatchOf([outer])), [[0, 0], [10, 10]]);
  });
});

describe("grips", () => {
  it("gives a line two ends that each move only themselves", () => {
    const e: Entity = { id: "l", kind: "line", layer: "0", geometry: { start: [0, 0], end: [10, 0] } };
    const g = grips(e);
    assert.equal(g.length, 2);
    assert.deepEqual(g[0]!.geometry([1, 2]), { start: [1, 2], end: [10, 0] });
    assert.deepEqual(g[1]!.geometry([3, 4]), { start: [0, 0], end: [3, 4] });
  });
  it("gives a polyline a grip per vertex, replacing just that vertex", () => {
    const g = grips(rect("r", 0, 0, 4, 2));
    assert.equal(g.length, 4);
    assert.deepEqual(g[2]!.geometry([9, 9]), { points: [[0, 0], [4, 0], [9, 9], [0, 2]] });
  });
  it("gives a circle a centre grip and a radius grip", () => {
    const e: Entity = { id: "c", kind: "circle", layer: "0", geometry: { center: [5, 5], radius: 3 } };
    const g = grips(e);
    assert.deepEqual(g[0]!.geometry([6, 7]), { center: [6, 7], radius: 3 });
    assert.deepEqual(g[1]!.at, [8, 5]);
    assert.deepEqual(g[1]!.geometry([5, 9]), { center: [5, 5], radius: 4 });
  });
  it("has none for shapes it cannot edit, and finds the nearest within a tolerance", () => {
    assert.deepEqual(grips(arc), []);
    assert.deepEqual(grips(hatchOf([])), []);
    const g = grips({ id: "l", kind: "line", layer: "0", geometry: { start: [0, 0], end: [10, 0] } });
    assert.equal(gripAt(g, [9.5, 0.3], 1), 1);
    assert.equal(gripAt(g, [5, 0], 1), -1);
  });
});
