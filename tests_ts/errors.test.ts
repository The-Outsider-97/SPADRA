import assert from "node:assert/strict";
import { test } from "node:test";
import { classify, oneLine, present, presentUnexpected } from "../src/client/errors.js";

const table = {
  bad_request: { category: "Input", recovery: "Check the value and try again." },
  conflict: { category: "Conflict", recovery: "Reload and try again." },
  storage_error: { category: "Storage", recovery: "Check the folder and the disk." },
  error: { category: "Unexpected", recovery: "Nothing was changed." },
};

test("an error code finds its own entry, then storage_*, then the default, then the built-in advice", () => {
  assert.equal(classify("conflict", table).category, "Conflict");
  assert.equal(classify("storage_full", table).category, "Storage");
  assert.equal(classify("brand_new_code", table).category, "Unexpected");
  assert.equal(classify("anything", undefined).category, "Unexpected");
  assert.match(classify("anything", {}).recovery, /Try again/);
});

test("the error's own hint wins over the category's advice, and its message is kept whole", () => {
  const p = present({ code: "conflict", message: "Door D01 is on sheet A101", hint: "Remove it from the sheet first" }, table);
  assert.deepEqual([p.category, p.message, p.recovery], ["Conflict", "Door D01 is on sheet A101", "Remove it from the sheet first"]);
  assert.equal(present({ code: "conflict", message: "x" }, table).recovery, "Reload and try again.");
});

test("a toast line reads kind, message, advice, with exactly one full stop after the message", () => {
  assert.equal(oneLine({ code: "c", category: "Input", message: "Width must be positive", recovery: "Try again." }), "Input: Width must be positive. Try again.");
  assert.equal(oneLine({ code: "c", category: "Input", message: "Width must be positive.", recovery: "Try again." }), "Input: Width must be positive. Try again.");
  assert.equal(oneLine({ code: "c", category: "Input", message: "Really?", recovery: "Say." }), "Input: Really? Say.");
});

test("a failure from the page itself names its cause and promises nothing it cannot know", () => {
  const p = presentUnexpected(new TypeError("Failed to fetch"), "Saving");
  assert.equal(p.code, "client");
  assert.match(p.message, /Saving failed unexpectedly \(Failed to fetch\)/);
  assert.match(presentUnexpected("boom", "That").message, /\(boom\)/);
});
