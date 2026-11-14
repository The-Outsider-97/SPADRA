import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DOOR_TYPES, FRAME_MATERIALS, JOINS, ALIGNS, MODES, PANEL_MATERIALS, PANEL_TYPES } from "../src/client/curtainwall.ts";
import { actionById } from "../src/client/shortcuts.ts";

/** The choices the dialog offers are the choices the server accepts: read them out of curtainwall.py so the two cannot drift apart. */
const py = readFileSync(new URL("../curtainwall.py", import.meta.url), "utf8");
const tuple = (name: string): string[] => {
  const m = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(py);
  assert.ok(m, `${name} not found in curtainwall.py`);
  return [...(m as RegExpExecArray)[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
};

test("the dialog's spacing rules, alignments and joins are the server's", () => {
  assert.deepEqual(MODES.map(([v]) => v), tuple("MODES"));
  assert.deepEqual(ALIGNS.map(([v]) => v), tuple("ALIGNS"));
  assert.deepEqual(JOINS.map(([v]) => v), tuple("JOINS"));
});

test("frame materials, panel types and door types are the server's", () => {
  assert.deepEqual(FRAME_MATERIALS, tuple("FRAME_MATERIALS"));
  assert.deepEqual(PANEL_TYPES, tuple("WALL_PANEL_TYPES"));          // a door only stands in a field, never as the wall's own panel setting
  assert.deepEqual(DOOR_TYPES, tuple("DOOR_TYPES"));
  assert.ok(PANEL_MATERIALS.includes("Glass"));
});

test("the curtain wall tool is a registered shortcut action", () => {
  const a = actionById("tool.curtainwall");
  assert.ok(a, "tool.curtainwall is registered");
  assert.match(a?.label ?? "", /curtain/i);
});
