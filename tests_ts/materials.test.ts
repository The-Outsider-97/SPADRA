import assert from "node:assert/strict";
import test from "node:test";
import { hexRgb01 } from "../src/client/palette.js";

test("conditional gradient HEX colors reach the shader as their actual RGB channels", () => {
  assert.deepEqual(hexRgb01("#123abc"), [18 / 255, 58 / 255, 188 / 255]);
  assert.deepEqual(hexRgb01("#fffe12"), [1, 254 / 255, 18 / 255]);
  assert.deepEqual(hexRgb01("#ee1ef2"), [238 / 255, 30 / 255, 242 / 255]);
  assert.throws(() => hexRgb01("no color"));
});
