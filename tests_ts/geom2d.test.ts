import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { alongFrom, angleBetween, polygonsBounds, rotateAbout, roundAngle, wallBand, distToSegment, entityBounds, formatDimension, pointInPolygon, polygonDistance, entityDistance, fitBox, insideBox, niceStep, normaliseRect, orthoConstrain, segmentIntersection, snapPoint } from "../src/client/geom2d.js";
import type { Entity } from "../src/client/types.js";

const line = (id: string, a: [number, number], b: [number, number]): Entity => ({ id, kind: "line", layer: "0", geometry: { start: a, end: b } });
const circle = (id: string, c: [number, number], r: number): Entity => ({ id, kind: "circle", layer: "0", geometry: { center: c, radius: r } });

describe("hit testing", () => {
  it("measures distance to a segment, including past its ends", () => {
    assert.equal(distToSegment([5, 3], [0, 0], [10, 0]), 3);
    assert.equal(distToSegment([-3, 4], [0, 0], [10, 0]), 5);
    assert.equal(distToSegment([1, 1], [2, 2], [2, 2]), Math.SQRT2);
  });
  it("measures circles by their rim and text by its box", () => {
    assert.equal(entityDistance(circle("c", [0, 0], 10), [13, 0]), 3);
    assert.equal(entityDistance(circle("c", [0, 0], 10), [4, 0]), 6);
    const text: Entity = { id: "t", kind: "text", layer: "0", geometry: { position: [0, 0], text: "AB", height: 10 } };
    assert.equal(entityDistance(text, [5, 5]), 0);
    assert.equal(entityDistance(text, [30, 0]), 30 - 12);
  });
  it("closes polylines only when they are closed", () => {
    const pts = [[0, 0], [10, 0], [10, 10]];
    const open: Entity = { id: "p", kind: "polyline", layer: "0", geometry: { points: pts, closed: false } };
    const closed: Entity = { id: "p", kind: "polyline", layer: "0", geometry: { points: pts, closed: true } };
    assert.ok(entityDistance(open, [5, 5]) > 4);
    assert.ok(entityDistance(closed, [5, 5]) < 1e-9);
  });
  it("selects by window only what is fully inside", () => {
    assert.ok(insideBox(line("a", [1, 1], [4, 4]), [0, 0], [5, 5]));
    assert.ok(!insideBox(line("a", [1, 1], [6, 4]), [0, 0], [5, 5]));
    assert.deepEqual(entityBounds(circle("c", [2, 2], 1)), [[1, 1], [3, 3]]);
  });
});

describe("snapping", () => {
  const scene = [line("a", [0, 0], [100, 0]), line("b", [50, -50], [50, 50])];
  it("prefers an endpoint over the grid", () => {
    assert.deepEqual(snapPoint([2, 1], scene, 5, 10), { point: [0, 0], kind: "end" });
  });
  it("finds midpoints, intersections and circle centres", () => {
    assert.equal(snapPoint([49, 2], scene, 5, null).kind, "intersection");
    assert.deepEqual(snapPoint([49, 2], scene, 5, null).point, [50, 0]);
    assert.equal(snapPoint([49, 30], scene, 5, null).kind, "none");
    assert.deepEqual(snapPoint([1, 1], [circle("c", [0, 0], 30)], 5, null), { point: [0, 0], kind: "center" });
    assert.deepEqual(snapPoint([49, 48], [line("m", [0, 0], [100, 100])], 5, null), { point: [50, 50], kind: "mid" });
  });
  it("falls back to the grid, then to the raw point", () => {
    assert.deepEqual(snapPoint([103, 197], [], 5, 50), { point: [100, 200], kind: "grid" });
    assert.deepEqual(snapPoint([103, 197], [], 5, null), { point: [103, 197], kind: "none" });
  });
  it("does not snap beyond the tolerance", () => {
    assert.equal(snapPoint([20, 20], scene, 5, null).kind, "none");
  });
});

describe("helpers", () => {
  it("picks 1-2-5 grid steps", () => {
    assert.equal(niceStep(0.7), 1);
    assert.equal(niceStep(1.1), 2);
    assert.equal(niceStep(3), 5);
    assert.equal(niceStep(5.1), 10);
    assert.equal(niceStep(130), 200);
    assert.equal(niceStep(0.03), 0.05);
    assert.equal(niceStep(-1), 1);
  });
  it("constrains to the dominant axis", () => {
    assert.deepEqual(orthoConstrain([0, 0], [10, 3]), [10, 0]);
    assert.deepEqual(orthoConstrain([0, 0], [2, -9]), [0, -9]);
  });
  it("normalises rectangles from any two corners", () => {
    assert.deepEqual(normaliseRect([10, 10], [4, 20]), { corner: [4, 10], width: 6, height: 10 });
  });
  it("intersects segments only where they cross", () => {
    assert.deepEqual(segmentIntersection([[0, 0], [10, 10]], [[0, 10], [10, 0]]), [5, 5]);
    assert.equal(segmentIntersection([[0, 0], [1, 1]], [[5, 0], [5, 10]]), null);
    assert.equal(segmentIntersection([[0, 0], [1, 0]], [[0, 1], [1, 1]]), null);
  });
  it("fits a box into a viewport", () => {
    const fit = fitBox([0, 0], [1000, 500], 548, 348, 24);
    assert.equal(fit.scale, 0.5);
    assert.deepEqual(fit.centre, [500, 250]);
  });
});

describe("dimension text", () => {
  it("groups thousands and drops meaningless zeros", () => {
    assert.equal(formatDimension("12000.00"), "12,000");
    assert.equal(formatDimension("1234.50"), "1,234.5");
    assert.equal(formatDimension("-4800"), "-4,800");
    assert.equal(formatDimension("R 250"), "R 250");
    assert.equal(formatDimension("4800 mm"), "4800 mm");
  });
});

describe("model walls in plan", () => {
  const wall: Array<[number, number]> = [[0, -125], [4000, -125], [4000, 125], [0, 125]];
  it("hits inside a footprint and measures to its edge outside", () => {
    assert.ok(pointInPolygon([2000, 0], wall));
    assert.ok(!pointInPolygon([2000, 200], wall));
    assert.equal(polygonDistance([2000, 0], wall), 0);
    assert.equal(polygonDistance([2000, 225], wall), 100);
    assert.equal(polygonDistance([4100, 0], wall), 100);
  });
  it("offers wall corners and edge middles as snap points", () => {
    assert.deepEqual(snapPoint([3, -120], [], 20, null, [wall]), { point: [0, -125], kind: "end" });
    assert.equal(snapPoint([4003, 130], [], 20, null, [wall]).kind, "end");
    assert.deepEqual(snapPoint([2003, 127], [], 20, null, [wall]), { point: [2000, 125], kind: "mid" });
    assert.equal(snapPoint([2003, 127], [], 20, null, []).kind, "none");
  });
});

describe("turning and stretching", () => {
  const close = (a: readonly number[], b: readonly number[]): void => a.forEach((v, i) => assert.ok(Math.abs(v - (b[i] ?? NaN)) < 1e-9, `${a} vs ${b}`));
  it("turns a point about a centre, counter-clockwise", () => {
    close(rotateAbout([2, 0], [1, 0], Math.PI / 2), [1, 1]);
    close(rotateAbout([3, 4], [3, 4], 1.234), [3, 4]);
  });
  it("measures the signed angle between two directions from a centre, the short way round", () => {
    assert.ok(Math.abs(angleBetween([0, 0], [1, 0], [0, 1]) - Math.PI / 2) < 1e-12);
    assert.ok(Math.abs(angleBetween([0, 0], [1, 0], [0, -1]) + Math.PI / 2) < 1e-12);
    assert.ok(Math.abs(angleBetween([5, 5], [6, 5], [6, 5])) < 1e-12);
    assert.ok(Math.abs(angleBetween([0, 0], [1, 0.01], [1, -0.01])) < 0.03);      // across the ±π seam it stays small
  });
  it("rounds an angle to whole degrees or to steps", () => {
    assert.ok(Math.abs(roundAngle(0.3, 1) - (17 * Math.PI) / 180) < 1e-12);
    assert.ok(Math.abs(roundAngle((40 * Math.PI) / 180, 15) - (45 * Math.PI) / 180) < 1e-12);
  });
  it("bounds several polygons, and none", () => {
    assert.deepEqual(polygonsBounds([[[0, 0], [2, 1]], [[-1, 3], [1, 1]]]), [[-1, 0], [2, 3]]);
    assert.equal(polygonsBounds([]), null);
  });
  it("draws a wall band of the given thickness about its centre line", () => {
    assert.deepEqual(wallBand([0, 0], [10, 0], 2), [[0, 1], [10, 1], [10, -1], [0, -1]]);
  });
  it("finds the point at a given length along a wall from its first end", () => {
    close(alongFrom([0, 0], [3, 4], 10), [6, 8]);
    close(alongFrom([1, 1], [1, 5], 2), [1, 3]);
  });
});
