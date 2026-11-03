import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { opensShortcutList, SHORTCUTS, toolHotkey } from "../src/client/shortcuts.js";

const field = (t: unknown): boolean => t === "field";
const ev = (key: string, over: Record<string, unknown> = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, target: "page", ...over });

test("? and F1 open the list, but never while typing and never with a modifier", () => {
  assert.equal(opensShortcutList(ev("?"), field), true);
  assert.equal(opensShortcutList(ev("F1"), field), true);
  assert.equal(opensShortcutList(ev("?", { target: "field" }), field), false);
  assert.equal(opensShortcutList(ev("?", { ctrlKey: true }), field), false);
  assert.equal(opensShortcutList(ev("a"), field), false);
});

test("every shortcut listed is handled by the code that is named, and the table has no duplicates", () => {
  const src = (f: string) => readFileSync(new URL(`../src/client/${f}`, import.meta.url), "utf8");
  const shell = src("shell.ts"), v2 = src("view2d.ts"), v3 = src("view3d.ts"), tl = src("timeline.ts");
  assert.match(shell, /k === "s"/); assert.match(shell, /k === "z"/); assert.match(shell, /k === "y"/);
  assert.match(v2, /e\.key === "Escape"/); assert.match(v2, /e\.key === "Enter"/); assert.match(v2, /e\.key === "c"/); assert.match(v2, /e\.key === "Delete"/); assert.match(v2, /e\.key === " "/);
  assert.match(v3, /e\.key === "Home"/); assert.match(v3, /e\.key === "Escape"/);
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown"]) assert.ok(v3.includes(`"${k}"`), k);
  assert.match(v3, /e\.ctrlKey/);
  for (const k of ["ArrowLeft", "ArrowRight", "Home", "End", "Delete"]) assert.ok(tl.includes(`"${k}"`), k);
  assert.match(tl, /e\.key === " "/);
  assert.equal(new Set(SHORTCUTS.map((s) => `${s.keys}|${s.where}`)).size, SHORTCUTS.length);
});

test("a plain letter selects a tool; a modifier never does", () => {
  const key = (k: string, over: Record<string, boolean> = {}) => toolHotkey({ key: k, ctrlKey: false, metaKey: false, altKey: false, ...over });
  const expected: Record<string, string> = { v: "select", l: "line", p: "polyline", r: "rectangle", o: "circle", d: "dimension", t: "text", w: "wall", g: "grid", h: "hatch", m: "mirror", q: "rotate" };
  for (const [k, tool] of Object.entries(expected)) { assert.equal(key(k), tool); assert.equal(key(k.toUpperCase()), tool); }
  assert.equal(key("z"), null); assert.equal(key("s", { ctrlKey: true }), null); assert.equal(key("l", { metaKey: true }), null); assert.equal(key("l", { altKey: true }), null);
});

test("the shell handles the single keys the table promises, and the canvas the pan and zoom keys", () => {
  const src = (f: string) => readFileSync(new URL(`../src/client/${f}`, import.meta.url), "utf8");
  const shell = src("shell.ts"), v2 = src("view2d.ts"), br = src("browser.ts");
  assert.match(shell, /e\.key === "\/"/); assert.match(shell, /e\.key === "2"/); assert.match(shell, /e\.key === "3"/); assert.match(shell, /e\.key === "f"/);
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) assert.ok(v2.includes(k), k);
  assert.match(v2, /e\.key === "\+"/); assert.match(v2, /e\.key === "-"/); assert.match(v2, /e\.key === "0"/);
  assert.match(br, /ContextMenu/); assert.match(br, /F10/);
});
