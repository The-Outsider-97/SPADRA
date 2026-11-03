import assert from "node:assert/strict";
import { test } from "node:test";
import { backupLabel, elementSummary, formatBytes, formatStamp } from "../src/client/project_dialogs.js";

test("sizes are exact below 1 KB and rounded above", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1023), "1023 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(150 * 1024), "150 KB");
  assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  assert.equal(formatBytes(null), "–");
  assert.equal(formatBytes(-1), "–");
  assert.equal(formatBytes(Number.NaN), "–");
});

test("stamps are shown as UTC to the minute and unknown text is left alone", () => {
  assert.equal(formatStamp("2026-11-02T13:55:01Z"), "2026-11-02 13:55 UTC");
  assert.equal(formatStamp("2026-11-02T13:55:01.123456+00:00"), "2026-11-02 13:55 UTC");
  assert.equal(formatStamp(null), "–");
  assert.equal(formatStamp("yesterday"), "yesterday");
});

test("element counts are listed largest first, ties by name", () => {
  assert.equal(elementSummary({}), "none");
  assert.equal(elementSummary({ wall: 3, slab: 1, door: 3 }), "door: 3, wall: 3, slab: 1");
});

test("a backup is described by when, which revision and how big", () => {
  assert.equal(backupLabel({ id: "20261102T135501Z-r4", created: "2026-11-02T13:55:01Z", name: "Hall", revision: 4, bytes: 2048 }), "2026-11-02 13:55 UTC · revision 4 · 2.0 KB");
});
