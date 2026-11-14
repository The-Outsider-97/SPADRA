import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ACTIONS, ChordReader, collide, formatBinding, Keymap, KEYMAP_STORAGE_KEY, parseBinding, PRESETS, presetBindings, reservedReason, SHORTCUTS, tokenOf, type KeyStore,
} from "../src/client/shortcuts.js";

const press = (key: string, over: Record<string, unknown> = {}) => ({ key, code: undefined as string | undefined, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...over });
const memory = (): KeyStore & { data: Map<string, string> } => { const data = new Map<string, string>(); return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => { data.set(k, v); } }; };
const src = (f: string) => readFileSync(new URL(`../src/client/${f}`, import.meta.url), "utf8");

test("a key press has one canonical name, whatever the layout or modifier order", () => {
  assert.equal(tokenOf(press("s", { ctrlKey: true })), "Ctrl+S");
  assert.equal(tokenOf(press("S", { ctrlKey: true, shiftKey: true })), "Ctrl+Shift+S");
  assert.equal(tokenOf(press("z", { metaKey: true })), "Ctrl+Z");                                  // Command counts as Ctrl
  assert.equal(tokenOf(press("?", { shiftKey: true })), "?");                                      // a shifted symbol already says so
  assert.equal(tokenOf(press("F9")), "F9");
  assert.equal(tokenOf(press("Escape")), "Esc");
  assert.equal(tokenOf(press("Tab", { shiftKey: true })), "Shift+Tab");
  assert.equal(tokenOf(press("5", { code: "Numpad5" })), "Num5");
  assert.equal(tokenOf(press("+", { code: "NumpadAdd" })), "Num+");
  assert.equal(tokenOf(press("Shift", { shiftKey: true })), null);                                 // a modifier alone is not a shortcut
  assert.equal(tokenOf(press("a", { altKey: true })), "Alt+A");
});

test("bindings are checked: a key, a combination or a short chord; anything else is refused", () => {
  assert.deepEqual(parseBinding("Ctrl+Shift+Z"), ["Ctrl+Shift+Z"]);
  assert.deepEqual(parseBinding("W A"), ["W", "A"]);
  assert.deepEqual(parseBinding("F12"), ["F12"]);
  for (const bad of ["", "Ctrl+", "Ctrl+Shift", "A B C D E", "F13", "Hyper+X", "Ctrl+Ctrl+S"]) assert.equal(parseBinding(bad), null, bad);
  assert.equal(formatBinding("W A"), "W then A");
  assert.ok(collide("W", "W A") && collide("W A", "W") && collide("W A", "W A") && !collide("W A", "W N") && !collide("W", "A"));
});

test("every action, in every preset, is a real action with a valid, unreserved binding, and no preset has a conflict", () => {
  const ids = new Set(ACTIONS.map((a) => a.id));
  assert.equal(ids.size, ACTIONS.length, "action ids are unique");
  for (const preset of PRESETS) {
    const bound = presetBindings(preset.id);
    for (const [action, keys] of Object.entries(bound)) {
      assert.ok(ids.has(action), `${preset.name}: ${action} is an action`);
      for (const k of keys) { assert.ok(parseBinding(k), `${preset.name}: ${k} is a binding`); assert.equal(reservedReason(k), null, `${preset.name}: ${k} is free to bind`); }
    }
    const map = new Keymap(null); map.usePreset(preset.id);
    assert.deepEqual(map.conflicts(), [], `${preset.name} has no conflicts`);
    for (const must of ["file.save", "edit.undo", "edit.redo"]) assert.ok(map.bindings(must).length, `${preset.name} binds ${must}`);
  }
  assert.deepEqual(PRESETS.map((p) => p.id), ["spadra", "autocad", "revit", "blender", "max"]);
});

test("the SPADRA preset keeps every key the application always had", () => {
  const map = new Keymap(null);
  const expected: Record<string, string> = { v: "select", l: "line", p: "polyline", r: "rectangle", o: "circle", d: "dimension", t: "text", w: "wall", g: "grid", h: "hatch", m: "mirror", q: "rotate" };
  for (const [k, tool] of Object.entries(expected)) assert.equal(map.actionFor(k.toUpperCase()), `tool.${tool}`);
  assert.equal(map.actionFor("Ctrl+S"), "file.save"); assert.equal(map.actionFor("Ctrl+Shift+S"), "file.saveAs"); assert.equal(map.actionFor("Ctrl+Z"), "edit.undo");
  assert.equal(map.actionFor("Ctrl+Y"), "edit.redo"); assert.equal(map.actionFor("Ctrl+Shift+Z"), "edit.redo"); assert.equal(map.actionFor("?"), "ui.shortcuts"); assert.equal(map.actionFor("F1"), "ui.shortcuts");
  assert.equal(map.actionFor("F9"), "view.snap"); assert.equal(map.actionFor("/"), "ui.search"); assert.equal(map.actionFor("2"), "view.to2d"); assert.equal(map.actionFor("3"), "view.to3d"); assert.equal(map.actionFor("F"), "view.fit");
});

test("the other presets follow the programs they are named for", () => {
  const at = (preset: string, binding: string): string | null => { const m = new Keymap(null); m.usePreset(preset); return m.actionFor(binding); };
  assert.equal(at("blender", "R"), "tool.rotate"); assert.equal(at("blender", "E"), "tool.extrude"); assert.equal(at("blender", "Home"), "view.fit"); assert.equal(at("blender", "Ctrl+Shift+Z"), "edit.redo"); assert.equal(at("blender", "Num5"), "view.projection");
  assert.equal(at("max", "Q"), "tool.select"); assert.equal(at("max", "E"), "tool.rotate"); assert.equal(at("max", "S"), "view.snap"); assert.equal(at("max", "G"), "view.grid"); assert.equal(at("max", "Z"), "view.fit");
  assert.equal(at("revit", "W A"), "tool.wall"); assert.equal(at("revit", "D I"), "tool.dimension"); assert.equal(at("revit", "G R"), "tool.grid"); assert.equal(at("revit", "M M"), "tool.mirror"); assert.equal(at("revit", "T X"), "tool.text");
  assert.equal(at("autocad", "L"), "tool.line"); assert.equal(at("autocad", "P L"), "tool.polyline"); assert.equal(at("autocad", "R E C"), "tool.rectangle"); assert.equal(at("autocad", "C"), "tool.circle"); assert.equal(at("autocad", "F9"), "view.snap"); assert.equal(at("autocad", "F7"), "view.grid");
  assert.equal(at("blender", "W"), "tool.select", "a preset's own key replaces the SPADRA one it collides with");
  assert.equal(at("blender", "L"), null, "a preset does not carry SPADRA's tool letters");
});

test("assigning a key: refused when it clashes, taken from the other when asked, kept alongside when asked, never when reserved", () => {
  const map = new Keymap(null);
  const refused = map.assign("tool.line", "W");
  assert.ok(refused.ok && refused.clashes.length === 1 && refused.clashes[0]!.actions[1] === "tool.wall");
  assert.deepEqual(map.bindings("tool.line"), ["L"], "nothing changed");
  map.assign("tool.line", "W", "steal");
  assert.deepEqual(map.bindings("tool.line"), ["L", "W"]); assert.deepEqual(map.bindings("tool.wall"), []); assert.deepEqual(map.conflicts(), []);
  map.assign("tool.wall", "W", "keep");
  assert.equal(map.conflicts().length, 1, "assign anyway leaves the clash visible");
  map.reset();
  assert.deepEqual(map.conflicts(), []); assert.deepEqual(map.bindings("tool.wall"), ["W"]);
  for (const bad of ["Esc", "Delete", "Backspace", "Enter", "Space", "Left", "Ctrl+Left", "Shift+Up", "PageDown", "Shift+F", "F5", "Ctrl+W"]) {
    const r = map.assign("tool.line", bad); assert.equal(r.ok, false, bad);
  }
  assert.equal(map.assign("tool.line", "Ctrl+Ctrl+Q").ok, false); assert.equal(map.assign("no.such", "K").ok, false);
});

test("a chord that starts another binding, or is started by one, is a clash", () => {
  const map = new Keymap(null);
  const r = map.assign("tool.line", "W A");                              // W is the wall tool
  assert.ok(r.ok && r.clashes.length === 1);
  map.usePreset("revit");
  const q = map.assign("tool.line", "W");                                 // W A and W N are the wall and window
  assert.ok(q.ok && q.clashes.length === 2);
});

test("replacing a key swaps it in place; removing and resetting go back to the preset", () => {
  const map = new Keymap(null);
  map.assign("tool.line", "K", "refuse", "L");
  assert.deepEqual(map.bindings("tool.line"), ["K"]); assert.equal(map.isCustom("tool.line"), true);
  map.remove("tool.line", "K"); assert.deepEqual(map.bindings("tool.line"), []);
  map.reset("tool.line"); assert.deepEqual(map.bindings("tool.line"), ["L"]); assert.equal(map.isCustom("tool.line"), false);
  map.assign("tool.line", "L");                                           // what the preset already says is no change
  assert.equal(map.hasCustom(), false);
  map.assign("tool.line", "K"); map.usePreset("max");
  assert.equal(map.hasCustom(), false, "changing preset drops changes made to the old one");
});

test("changes persist across a reload, and bad stored data is dropped, not trusted", () => {
  const store = memory();
  const a = new Keymap(store);
  a.usePreset("blender"); a.assign("tool.line", "Shift+L"); a.assign("view.fit", "Ctrl+Alt+F");
  assert.ok(store.data.get(KEYMAP_STORAGE_KEY));
  const b = new Keymap(store);
  assert.equal(b.preset, "blender"); assert.deepEqual(b.bindings("tool.line"), ["Shift+L"]); assert.ok(b.bindings("view.fit").includes("Ctrl+Alt+F"));
  store.data.set(KEYMAP_STORAGE_KEY, JSON.stringify({ preset: "nope", overrides: { "tool.line": ["Esc", "K", 5, "Ctrl+"], "no.such": ["A"], "tool.wall": "W" } }));
  const c = new Keymap(store);
  assert.equal(c.preset, "spadra"); assert.deepEqual(c.bindings("tool.line"), ["K"]); assert.deepEqual(c.bindings("tool.wall"), ["W"]);
  store.data.set(KEYMAP_STORAGE_KEY, "{not json");
  assert.equal(new Keymap(store).preset, "spadra");
  const broken: KeyStore = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  const d = new Keymap(broken); d.assign("tool.line", "K");
  assert.deepEqual(d.bindings("tool.line"), ["L", "K"], "storage that throws still lets the page work");
  assert.equal(c.apply(a.export()), true); assert.equal(c.preset, "blender");
  assert.equal(c.apply("[]"), true); assert.equal(c.apply("nonsense"), false);
});

test("search finds an action by its name, a synonym, its group or its keys", () => {
  const map = new Keymap(null);
  assert.ok(map.search("rotate").some((a) => a.id === "tool.rotate"));
  assert.ok(map.search("turn").some((a) => a.id === "tool.rotate"));
  assert.ok(map.search("ctrl shift s").some((a) => a.id === "file.saveAs"));
  assert.ok(map.search("f9").some((a) => a.id === "view.snap"));
  assert.ok(map.search("view").every((a) => /view/i.test(`${a.label} ${a.group}`) || /view/i.test(a.words ?? "") || map.bindings(a.id).join(" ").toLowerCase().includes("view")));
  assert.equal(map.search("zzzz nothing").length, 0);
  assert.equal(map.search("").length, ACTIONS.length);
});

test("a chord runs when its last key arrives, a single key runs at once, and a stale or wrong key starts over", () => {
  const map = new Keymap(null); map.usePreset("revit");
  const reader = new ChordReader(map);
  assert.deepEqual(reader.feed("W", 0), { kind: "wait", typed: ["W"] });
  assert.deepEqual(reader.feed("A", 400), { kind: "run", action: "tool.wall" });
  assert.deepEqual(reader.feed("W", 1000), { kind: "wait", typed: ["W"] });
  assert.deepEqual(reader.feed("Z", 1200), { kind: "wait", typed: ["Z"] }, "no binding starts W Z, so Z starts over (Z F is zoom to fit)");
  assert.deepEqual(reader.feed("K", 1300), { kind: "none" }, "and K finishes nothing");
  assert.deepEqual(reader.feed("W", 2000), { kind: "wait", typed: ["W"] });
  assert.deepEqual(reader.feed("N", 4000), { kind: "none" }, "a key after the time-out does not finish the old chord");
  assert.deepEqual(reader.feed("W", 5000), { kind: "wait", typed: ["W"] });
  assert.deepEqual(reader.feed("W", 5100), { kind: "wait", typed: ["W"] }, "a repeated first key starts the chord again");
  const plain = new ChordReader(new Keymap(null));
  assert.deepEqual(plain.feed("Ctrl+S", 0), { kind: "run", action: "file.save" });
  assert.deepEqual(plain.feed("Q", 0), { kind: "run", action: "tool.rotate" });
  assert.deepEqual(plain.feed("K", 0), { kind: "none" });
});

test("every action has a handler in the shell, and the shell reads the keymap rather than fixed letters", () => {
  const shell = src("shell.ts");
  for (const a of ACTIONS) {
    if (a.id.startsWith("tool.")) continue;
    assert.ok(shell.includes(`case "${a.id}"`), `the shell runs ${a.id}`);
  }
  assert.match(shell, /this\.chords\.feed\(/); assert.match(shell, /recordingKey/); assert.match(shell, /pickTool\(/);
  assert.ok(!/HOTKEYS|toolHotkey|opensShortcutList/.test(shell), "no fixed key table is left in the shell");
  const tools = [...src("store.ts").matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
  for (const a of ACTIONS.filter((x) => x.id.startsWith("tool."))) assert.ok(tools.includes(a.id.slice(5)), `${a.id} is a tool the store knows`);
});

test("the keys the views keep for themselves are handled where the list says, and are listed once", () => {
  const v2 = src("view2d.ts"), v3 = src("view3d.ts"), tl = src("timeline.ts"), br = src("browser.ts");
  assert.match(v2, /e\.key === "Escape"/); assert.match(v2, /e\.key === "Enter"/); assert.match(v2, /e\.key === "c"/); assert.match(v2, /e\.key === "Delete"/); assert.match(v2, /e\.key === " "/);
  assert.match(v3, /e\.key === "Home"/); assert.match(v3, /e\.key === "Escape"/);
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown"]) assert.ok(v3.includes(`"${k}"`), k);
  assert.match(v3, /e\.ctrlKey/);
  for (const k of ["ArrowLeft", "ArrowRight", "Home", "End", "Delete"]) assert.ok(tl.includes(`"${k}"`), k);
  assert.match(tl, /e\.key === " "/);
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) assert.ok(v2.includes(k), k);
  assert.match(v2, /e\.key === "\+"/); assert.match(v2, /e\.key === "-"/); assert.match(v2, /e\.key === "0"/);
  assert.match(br, /ContextMenu/); assert.match(br, /F10/);
  assert.equal(new Set(SHORTCUTS.map((s) => `${s.keys}|${s.where}`)).size, SHORTCUTS.length);
});
