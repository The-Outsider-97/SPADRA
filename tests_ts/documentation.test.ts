import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  cutPath, cutWeight, edgePath, inTriangle, insideLoops, itemContains, levelLabel, markArrows, markLabel, projectionAt, snapPolygons, surfacePath, swingPath, visibleLevels,
} from "../src/client/projection.js";
import { formatCell, sortRows } from "../src/client/schedule.js";
import type { ProjItem, Projection, SectionMark } from "../src/client/types.js";

const item = (id: string, over: Partial<ProjItem> = {}): ProjItem => ({ id, category: "wall", name: id, depth: 0, tris: [], edges: [], cut: [], ...over });
const square = (x: number, y: number, s: number): [number, number][] => [[x, y], [x + s, y], [x + s, y + s], [x, y + s]];
const projection = (items: ProjItem[], over: Partial<Projection> = {}): Projection => ({ kind: "section", items, levels: [], bounds: null, ...over });

describe("hit testing on a projection", () => {
  it("knows inside a triangle from outside, on either winding", () => {
    assert.ok(inTriangle([1, 1], [0, 0, 4, 0, 0, 4]));
    assert.ok(inTriangle([1, 1], [0, 0, 0, 4, 4, 0]));
    assert.ok(!inTriangle([3, 3], [0, 0, 4, 0, 0, 4]));
  });

  it("allows a tolerance around a triangle's edge, and no more", () => {
    assert.ok(!inTriangle([5, 1], [0, 0, 4, 0, 0, 4]));
    assert.ok(inTriangle([4.5, 0], [0, 0, 4, 0, 0, 4], 1));
    assert.ok(!inTriangle([6, 0], [0, 0, 4, 0, 0, 4], 1));
  });

  it("treats a loop inside a loop as a hole (even-odd), as the cut of a wall with a window is", () => {
    const loops = [square(0, 0, 10), square(3, 3, 4)];
    assert.ok(insideLoops([1, 1], loops));
    assert.ok(!insideLoops([5, 5], loops));
    assert.ok(!insideLoops([20, 20], loops));
  });

  it("finds an element by its surface, its cut, or (with a tolerance) its drawn lines", () => {
    const a = item("a", { tris: [[0, 0, 10, 0, 0, 10], [10, 0, 10, 10, 0, 10]], edges: [[0, 0, 10, 0]] });
    assert.ok(itemContains(a, [5, 5]));
    assert.ok(!itemContains(a, [12, 5]));
    assert.ok(!itemContains(a, [10.4, -1]));
    assert.ok(itemContains(a, [5, -0.4], 0.5), "near a drawn line");
    const c = item("c", { cut: [square(0, 0, 10), square(3, 3, 4)] });
    assert.ok(itemContains(c, [1, 1]));
    assert.ok(!itemContains(c, [5, 5]), "not in the hole");
  });

  it("picks the nearest element where two overlap, and a cut over anything beyond it", () => {
    const far = item("far", { tris: [[0, 0, 10, 0, 0, 10], [10, 0, 10, 10, 0, 10]] });
    const near = item("near", { tris: [[0, 0, 5, 0, 0, 5]] });                       // painted after, so it is on top
    const cut = item("cut", { cut: [square(0, 0, 3)] });
    assert.equal(projectionAt(projection([far, near]), [1, 1]), "near");
    assert.equal(projectionAt(projection([far, near]), [8, 8]), "far");
    assert.equal(projectionAt(projection([far, cut, near]), [1, 1]), "cut", "the cut is what the viewer sees first");
    assert.equal(projectionAt(projection([far]), [20, 20]), null);
  });

  it("offers the corners of cuts and the ends of drawn lines for snapping", () => {
    const polys = snapPolygons(projection([item("a", { cut: [square(0, 0, 2)], edges: [[5, 5, 9, 5]] })]));
    assert.equal(polys.length, 2);
    assert.deepEqual(polys[0]?.length, 4);
    assert.deepEqual(polys[1], [[5, 5], [9, 5]]);
  });
});

describe("labels and marks", () => {
  it("labels a level with its elevation in metres, with a sign", () => {
    assert.equal(levelLabel("Level 02", 3200, 1), "Level 02  +3.200");
    assert.equal(levelLabel("Basement", -2500, 1), "Basement  -2.500");
    assert.equal(levelLabel("Level 02", 320, 10), "Level 02  +3.200", "in centimetres");
    assert.equal(levelLabel("Level 01", 0, 1), "Level 01  +0.000");
  });

  it("puts the section's letter in its bubble", () => {
    assert.equal(markLabel("Section A-A"), "A");
    assert.equal(markLabel("Section c-c"), "C");
    assert.equal(markLabel("Long section"), "L");
    assert.equal(markLabel("Section 2"), "S", "a name that is not X-X falls back to its first letter");
  });

  it("draws an arrowhead at each end of a section line pointing the way it looks", () => {
    const mark: SectionMark = { id: "s", name: "Section A-A", a: [0, 0], b: [0, 10], look: [-1, 0], looking: "west", depth: 30 };
    const [first, second] = markArrows(mark, 4);
    assert.deepEqual(first?.from, [0, 0]);
    assert.deepEqual(first?.tip, [-4, 0]);
    assert.deepEqual(second?.from, [0, 10]);
    assert.deepEqual(second?.tip, [-4, 10]);
    for (const a of [first, second]) {                           // the two barbs are mirror images either side of the arrow's shaft
      assert.ok(a);
      assert.ok(Math.abs(a.left[0] - a.right[0]) < 1e-9);
      assert.ok(Math.abs((a.left[1] + a.right[1]) / 2 - a.tip[1]) < 1e-9);
      assert.ok(a.left[0] > a.tip[0], "barbs are behind the tip");
    }
  });

  it("draws only the levels that are near what the view shows", () => {
    const pr = projection([], { bounds: [[0, 0], [8000, 3000]], levels: [{ name: "L1", z: 0 }, { name: "L2", z: 3200 }, { name: "Roof", z: 6400 }] });
    assert.deepEqual(visibleLevels(pr, 1).map((l) => l.name), ["L1", "L2"]);
    assert.deepEqual(visibleLevels(projection([]), 1), []);
  });

  it("weights the cut so structure is solid and glazing is light", () => {
    assert.equal(cutWeight("wall"), 1);
    assert.ok(cutWeight("window") < cutWeight("door"));
    assert.ok(cutWeight("door") < 1);
  });
});

describe("paths", () => {
  const id = (p: [number, number]): [number, number] => p;
  it("makes one closed subpath per triangle, so a filled item has no seams", () => {
    const d = surfacePath(item("a", { tris: [[0, 0, 4, 0, 0, 4], [4, 0, 4, 4, 0, 4]] }), id);
    assert.equal(d, "M0 0L4 0L0 4ZM4 0L4 4L0 4Z");
  });
  it("makes one subpath per drawn line", () => {
    assert.equal(edgePath(item("a", { edges: [[0, 0, 4, 0], [4, 0, 4, 4]] }), id), "M0 0L4 0M4 0L4 4");
  });
  it("makes one closed subpath per loop of the cut, and applies the mapping", () => {
    assert.equal(cutPath(item("a", { cut: [square(0, 0, 2), square(1, 1, 0.5)] }), id), "M0 0L2 0L2 2L0 2ZM1 1L1.5 1L1.5 1.5L1 1.5Z");
    assert.equal(cutPath(item("a", { cut: [square(0, 0, 2)] }), ([x, y]) => [x * 10, -y * 10]), "M0 0L20 0L20 -20L0 -20Z");
  });
});

describe("schedule tables", () => {
  it("shows numbers without trailing zeros and text as it is", () => {
    assert.equal(formatCell(1.89), "1.89");
    assert.equal(formatCell(2.1), "2.1");
    assert.equal(formatCell(900), "900");
    assert.equal(formatCell(0.1 + 0.2), "0.3");
    assert.equal(formatCell("Timber"), "Timber");
    assert.equal(formatCell(undefined), "");
  });

  it("sorts numbers as numbers and names with digits in natural order", () => {
    const rows = [{ no: "D10", w: 900 }, { no: "D2", w: 1200 }, { no: "D1", w: 90 }];
    assert.deepEqual(sortRows(rows, "no", 1).map((r) => r.no), ["D1", "D2", "D10"]);
    assert.deepEqual(sortRows(rows, "w", 1).map((r) => r.w), [90, 900, 1200]);
    assert.deepEqual(sortRows(rows, "w", -1).map((r) => r.w), [1200, 900, 90]);
    assert.deepEqual(rows.map((r) => r.no), ["D10", "D2", "D1"], "the rows the server sent are not reordered in place");
  });
});

describe("opening direction", () => {
  it("draws a door's leaf and swing as one path, and nothing for a symbol without a swing", () => {
    const d = swingPath({ kind: "door", hinge: [0, 0], leaf_end: [0, 900], radius: 900, start_angle: 0, end_angle: Math.PI / 2 });
    assert.ok(d.startsWith("M0 0L0 900M900 0A900 900 0 0 1 "), d);
    assert.equal(swingPath({ kind: "window", a: [0, 0], b: [1200, 0] }), "", "a fixed window opens nowhere");
    assert.equal(swingPath({ kind: "door" }), "", "a view that hides the direction sends no swing");
    assert.equal(swingPath(undefined), "");
  });

  it("uses the large-arc flag only past a half turn", () => {
    assert.match(swingPath({ kind: "door", hinge: [0, 0], leaf_end: [1, 0], radius: 1, start_angle: 0, end_angle: Math.PI * 1.5 }), /A1 1 0 1 1 /);
  });
});
