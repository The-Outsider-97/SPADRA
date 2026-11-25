import assert from "node:assert/strict";
import { test } from "node:test";
import { parseRange, rangeEdit, rangeText, rangeTitle } from "../src/client/view_range_ui.js";
import type { ViewRange } from "../src/client/types.js";

const range: ViewRange = {
  level: "l2", level_name: "Level 02", reference_level: "l2", reference_name: "Level 02", level_z: 3.2, reference_z: 3.2,
  height: 1.5, depth: 1.0, top_above_cut: 1.5, bottom_offset: -0.2, depth_offset: -1.0, bottom: 0.2,
  cut_z: 4.7, top_z: 6.2, bottom_z: 3.0, depth_z: 2.2, valid: true, snap_context: false,
};

test("metres are shown in the project's unit without trailing zeros", () => {
  assert.equal(rangeText(1.5, "mm"), "1500");
  assert.equal(rangeText(1.5, "m"), "1.5");
  assert.equal(rangeText(1.2345, "cm"), "123.45");
  assert.equal(rangeText(0, "mm"), "0");
});

test("typed values are read in the project's unit, with an optional unit and a decimal comma", () => {
  assert.deepEqual(parseRange("1500", "mm"), { ok: true, value: 1500 });
  assert.deepEqual(parseRange(" 1,5 m ", "mm"), { ok: true, value: 1500 });
  assert.deepEqual(parseRange("150 cm", "mm"), { ok: true, value: 1500 });
  assert.deepEqual(parseRange("1.5", "m"), { ok: true, value: 1.5 });
  assert.deepEqual(parseRange("-200", "mm"), { ok: true, value: -200 });
  assert.deepEqual(parseRange(".5 m", "m"), { ok: true, value: 0.5 });
});

test("anything that is not a number is refused with a sentence that says what to type", () => {
  for (const bad of ["", "abc", "1.5.2", "1e3", "NaN", "Infinity", "12 ft", "--3", "1 5"]) {
    const out = parseRange(bad, "mm");
    assert.equal(out.ok, false, bad);
    if (!out.ok) assert.match(out.message, /number|range/i);
  }
  const huge = parseRange("5000000", "mm");                       // 5 km
  assert.equal(huge.ok, false);
});

test("View Height edits the cut offset; View Depth is typed below the level and stored as an offset from it", () => {
  assert.deepEqual(rangeEdit("height", 1200), { key: "cut_offset", value: 1200 });
  assert.deepEqual(rangeEdit("depth", 1000), { key: "range_depth", value: -1000 });
  assert.deepEqual(rangeEdit("depth", -500), { key: "range_depth", value: 500 });
  assert.equal(Object.is(rangeEdit("depth", 0).value, -0), false);
});

test("the tooltips name the reference level and the absolute elevation", () => {
  const height = rangeTitle("height", range, "mm"), depth = rangeTitle("depth", range, "mm");
  assert.match(height, /1500 mm above Level 02/);
  assert.match(height, /4700 mm absolute/);
  assert.match(depth, /1000 mm below Level 02/);
  assert.match(depth, /halftone/);
});
