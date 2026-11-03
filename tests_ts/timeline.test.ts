import { test } from "node:test";
import assert from "node:assert/strict";
import { describeValue, formatTime, groupLanes, laneLabel, rulerStep, timeFromX } from "../src/client/timeline.js";
import { exportUnavailable, frameName, frameTimes } from "../src/client/animation_export.js";
import type { AnimLane } from "../src/client/types.js";

test("a key reads as a move in metres, scale factors, a turn in degrees, or shown / hidden", () => {
  assert.equal(describeValue("translation", [0, 0.5, 2]), "0.00, 0.50, 2.00 m");
  assert.equal(describeValue("scale", [2, 1, 1]), "×2.00, ×1.00, ×1.00");
  assert.equal(describeValue("turn", 90), "90.00°");
  assert.equal(describeValue("visibility", false), "hidden");
  assert.equal(describeValue("visibility", true), "shown");
  assert.equal(describeValue("parameter.height", 3), "3");
});

test("lane labels are plain words; unknown properties lose their prefix", () => {
  assert.equal(laneLabel("translation"), "Move");
  assert.equal(laneLabel("turn"), "Turn");
  assert.equal(laneLabel("visibility"), "Show / hide");
  assert.equal(laneLabel("parameter.height"), "height");
});

test("the readout names the frame as well as the second", () => {
  assert.equal(formatTime(1.5, 24), "1.50 s · frame 36");
  assert.equal(formatTime(0, 30), "0.00 s · frame 0");
});

test("time under the pointer snaps to a whole frame and stays inside the animation", () => {
  assert.equal(timeFromX(500, 1000, 4, 24), 2);
  assert.equal(timeFromX(-30, 1000, 4, 24), 0);
  assert.equal(timeFromX(5000, 1000, 4, 24), 4);
  assert.equal(timeFromX(251, 1000, 4, 10), 1);                 // 1.004 s at 10 frames/s is frame 10 = 1.0 s
  assert.equal(timeFromX(10, 0, 4, 24), 0);
  assert.equal(timeFromX(10, 100, 0, 24), 0);
});

test("ruler labels are at least the minimum distance apart and use round steps", () => {
  assert.equal(rulerStep(5, 500), 1);                           // 100 px per second: 0.5 s is 50 px (< 56), so 1 s is the first step wide enough
  assert.equal(rulerStep(60, 600), 10);
  for (const [d, w] of [[2, 300], [10, 900], [120, 800], [3600, 400]] as const) {
    const step = rulerStep(d, w);
    assert.ok(step * (w / d) >= 56 || step === 300, `${d} s in ${w} px`);
  }
});

test("lanes are grouped by element in first-seen order and sorted by property within an element", () => {
  const lane = (object_id: string, property: string): AnimLane => ({ object_id, property, editable: true, interpolation: "smooth", keys: [] });
  const groups = groupLanes([lane("b", "visibility"), lane("a", "scale"), lane("b", "translation"), lane("a", "turn"), lane("b", "parameter.x")]);
  assert.deepEqual(groups.map((g) => g.object_id), ["b", "a"]);
  assert.deepEqual(groups[0]?.lanes.map((l) => l.property), ["translation", "visibility", "parameter.x"]);
  assert.deepEqual(groups[1]?.lanes.map((l) => l.property), ["turn", "scale"]);
});

test("frame times run from 0 to the end inclusive at the frame rate", () => {
  assert.deepEqual(frameTimes(1, 4), [0, 0.25, 0.5, 0.75, 1]);
  assert.equal(frameTimes(5, 24).length, 121);
  assert.equal(frameTimes(5, 24).at(-1), 5);
  assert.deepEqual(frameTimes(0.1, 10), [0, 0.1]);
});

test("frame files sort in order and have enough digits for the whole sequence", () => {
  assert.equal(frameName(0, 121), "frame_0001.png");
  assert.equal(frameName(120, 121), "frame_0121.png");
  assert.equal(frameName(0, 12345), "frame_00001.png");
});

test("exporting is refused with a reason where the browser cannot do it", () => {
  assert.match(exportUnavailable("png", false) ?? "", /WebGL 2/);
  assert.equal(exportUnavailable("png", true), null);
});
