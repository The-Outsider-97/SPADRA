import { test } from "node:test";
import assert from "node:assert/strict";
import { parseConstraints, parseFormulas, parseParameters } from "../src/client/agents_ui.ts";
import { cell, KIND_LABEL, STATUS_LABEL } from "../src/client/assistant.ts";

test("parameter lines become quantities, and a malformed line is named", () => {
  const ok = parseParameters("width = 3 m\nthickness=200 mm\n\ncount = 4");
  assert.equal(ok.error, null);
  assert.deepEqual(ok.parameters, { width: { value: 3, unit: "m" }, thickness: { value: 200, unit: "mm" }, count: 4 });
  assert.match(parseParameters("width 3 m").error ?? "", /line 1/);
  assert.match(parseParameters("a = 1\n= 2").error ?? "", /line 2/);
  assert.equal(parseParameters("x = 1,5 m").parameters["x"].value, 1.5);
});

test("constraints with a number and unit on the right become structured limits; expressions pass through", () => {
  const c = parseConstraints("height <= 4 m\nthickness >= 250 mm\na + b < c\nratio == 2");
  assert.equal(c.error, null);
  assert.deepEqual(c.constraints[0], { left: "height", op: "<=", right: { value: 4, unit: "m" } });
  assert.deepEqual(c.constraints[1], { left: "thickness", op: ">=", right: { value: 250, unit: "mm" } });
  assert.equal(c.constraints[2], "a + b < c");
  assert.deepEqual(c.constraints[3], { left: "ratio", op: "==", right: 2 });
  assert.match(parseConstraints("height 4").error ?? "", /line 1/);
});

test("formula lines need a name and an expression", () => {
  assert.deepEqual(parseFormulas("v = a * b\nw = v / 2").formulas, { v: "a * b", w: "v / 2" });
  assert.match(parseFormulas("a * b").error ?? "", /line 1/);
});

test("assistant labels distinguish every kind of answer the master prompt names", () => {
  for (const k of ["success", "proposal", "explanation", "analysis", "calculation", "unsupported", "failed"]) assert.ok(KIND_LABEL[k], k);
  assert.notEqual(new Set(Object.values(KIND_LABEL)).size, 1);
  for (const s of ["pending", "executed", "rejected", "cancelled", "stale", "failed"]) assert.ok(STATUS_LABEL[s], s);
  assert.equal(cell(null), "–");
  assert.equal(cell(3.2, "m"), "3.2 m");
  assert.equal(cell("X: 0 mm"), "X: 0 mm");
});
