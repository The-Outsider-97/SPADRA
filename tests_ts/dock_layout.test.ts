import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activate, allPanels, close, CURRENT_KEY, defaultLayout, DockError, findStack, fit, locate, MIN_CENTER, MIN_FLOAT, MIN_REGION, MIN_WEIGHT, moveFloating, NAMED_PREFIX, parse,
  place, problems, regionSizes, reopen, reorderTab, resetPanel, resizeFloating, resizeSplit, serialize, setRegionSize, SIDES, stacksOf, toggleRegion, validate, WorkspaceStore,
  type DropTarget, type KeyStore, type Layout, type PanelDef, type Side, type Stack,
} from "../src/client/dock_layout.js";

const PANELS: PanelDef[] = [
  { id: "project", title: "Project Browser", home: "left", order: 0 },
  { id: "elements", title: "Element Browser", home: "left", order: 1 },
  { id: "properties", title: "Properties", home: "right", order: 0 },
];
const VIEW = { width: 1600, height: 900 };
const fresh = (): Layout => defaultLayout(PANELS);
const sound = (l: Layout, panels: PanelDef[] = PANELS): void => assert.deepEqual(problems(l, panels), []);
const stackOf = (l: Layout, panel: string): Stack => { const p = locate(l, panel); assert.equal(p?.where, "stack"); return (p as { stack: Stack }).stack; };
const sideOf = (l: Layout, panel: string): Side => { const p = locate(l, panel); assert.equal(p?.where, "stack"); return (p as { side: Side }).side; };
const frozen = (l: Layout): string => JSON.stringify(l);
class Memory implements KeyStore {
  data = new Map<string, string>();
  get length(): number { return this.data.size; }
  key(i: number): string | null { return [...this.data.keys()][i] ?? null; }
  getItem(k: string): string | null { return this.data.get(k) ?? null; }
  setItem(k: string, v: string): void { this.data.set(k, v); }
  removeItem(k: string): void { this.data.delete(k); }
}

test("the default workspace puts each panel in its home dock, tabbed where they share one", () => {
  const l = fresh();
  sound(l);
  assert.deepEqual(stackOf(l, "project").panels, ["project", "elements"]);
  assert.equal(stackOf(l, "project").active, "project");
  assert.equal(sideOf(l, "properties"), "right");
  assert.deepEqual([...allPanels(l)].sort(), ["elements", "project", "properties"]);
  assert.equal(l.floating.length + l.hidden.length, 0);
});

test("a panel can be tabbed into another dock, and the dock it left is gone when it was alone", () => {
  const l = fresh();
  const target = stackOf(l, "properties").id;
  const a = place(l, "project", { kind: "tab", stack: target });
  sound(a);
  assert.deepEqual(stackOf(a, "properties").panels, ["properties", "project"]);
  assert.equal(stackOf(a, "properties").active, "project");                        // the one just dropped is the one shown
  assert.deepEqual(stackOf(a, "elements").panels, ["elements"]);
  const b = place(a, "elements", { kind: "tab", stack: target });
  sound(b);
  assert.equal(b.regions.left.root, null);                                         // nothing left in the left dock: it is empty, not a ghost
  assert.deepEqual(stackOf(b, "properties").panels, ["properties", "project", "elements"]);
});

test("every operation leaves the layout it was given untouched", () => {
  const l = fresh();
  const before = frozen(l);
  const s = stackOf(l, "properties").id;
  place(l, "project", { kind: "tab", stack: s });
  place(l, "elements", { kind: "split", stack: s, edge: "bottom" });
  place(l, "elements", { kind: "float", x: 10, y: 10 });
  close(l, "elements");
  reorderTab(l, stackOf(l, "project").id, "elements", 0);
  activate(l, stackOf(l, "project").id, "elements");
  setRegionSize(l, "left", 400);
  toggleRegion(l, "left");
  fit(l, { width: 500, height: 300 });
  assert.equal(frozen(l), before);
});

test("tabs can be switched and reordered, and switching changes nothing else", () => {
  const l = fresh();
  const id = stackOf(l, "project").id;
  const a = activate(l, id, "elements");
  assert.equal(stackOf(a, "project").active, "elements");
  assert.deepEqual({ ...a, regions: { ...a.regions, left: l.regions.left } }.floating, l.floating);
  const b = reorderTab(a, id, "elements", 0);
  assert.deepEqual(stackOf(b, "project").panels, ["elements", "project"]);
  assert.equal(stackOf(b, "project").active, "elements");                          // the active tab survives a reorder
  assert.throws(() => activate(l, id, "properties"), DockError);
});

test("a panel can be docked beside or below another, in either direction", () => {
  const l = fresh();
  const props = stackOf(l, "properties").id;
  const below = place(l, "elements", { kind: "split", stack: props, edge: "bottom" });
  sound(below);
  const root = below.regions.right.root;
  assert.equal(root?.kind, "split");
  assert.equal(root?.kind === "split" && root.dir, "column");
  assert.deepEqual(root?.kind === "split" ? root.weights : [], [0.5, 0.5]);
  assert.deepEqual(stacksOf(root ?? null).map((s) => s.panels), [["properties"], ["elements"]]);
  const beside = place(below, "project", { kind: "split", stack: stackOf(below, "properties").id, edge: "left" });   // across: a row inside the column
  sound(beside);
  assert.equal(beside.regions.left.root, null);                                    // project left the left dock, which had only it and elements: elements went with the move above
  const above = place(fresh(), "project", { kind: "split", stack: props, edge: "top" });
  assert.deepEqual(stacksOf(above.regions.right.root).map((s) => s.panels[0]), ["project", "properties"]);
});

test("splitting beside a stack that is already in a split of that direction shares out its space and adds no nesting", () => {
  let l = place(fresh(), "elements", { kind: "split", stack: stackOf(fresh(), "properties").id, edge: "bottom" });
  l = place(l, "project", { kind: "split", stack: stackOf(l, "elements").id, edge: "bottom" });
  sound(l);
  const root = l.regions.right.root;
  assert.equal(root?.kind === "split" && root.children.length, 3);
  assert.ok(root?.kind === "split" && Math.abs(root.weights.reduce((a, b) => a + b, 0) - 1) < 1e-9);
  assert.deepEqual(root?.kind === "split" ? root.weights : [], [0.5, 0.25, 0.25]);
});

test("dropping on a dock region adds a stack to it, and opens it if it was collapsed", () => {
  let l = toggleRegion(fresh(), "right");
  assert.equal(l.regions.right.collapsed, true);
  l = place(l, "elements", { kind: "region", side: "right" });
  sound(l);
  assert.equal(l.regions.right.collapsed, false);
  assert.deepEqual(stacksOf(l.regions.right.root).map((s) => s.panels), [["properties"], ["elements"]]);
  l = place(l, "project", { kind: "region", side: "bottom" });                     // an empty region simply gets the stack
  assert.deepEqual(stacksOf(l.regions.bottom.root).map((s) => s.panels), [["project"]]);
  l = place(l, "properties", { kind: "region", side: "bottom" });
  assert.equal(l.regions.bottom.root?.kind === "split" && l.regions.bottom.root.dir, "row");   // top and bottom docks lay their stacks side by side
});

test("a panel can float, be moved and resized there, hold tabs, and be docked again", () => {
  let l = place(fresh(), "elements", { kind: "float", x: 120, y: 80, w: 100, h: 50 });
  sound(l);
  const f = l.floating[0];
  assert.deepEqual([f?.x, f?.y, f?.w, f?.h], [120, 80, MIN_FLOAT.w, MIN_FLOAT.h]);   // never smaller than a usable window
  assert.equal(locate(l, "elements")?.where, "floating");
  l = moveFloating(l, f?.id ?? "", 300, 200);
  l = resizeFloating(l, f?.id ?? "", 500, 10);
  assert.deepEqual([l.floating[0]?.x, l.floating[0]?.y, l.floating[0]?.w, l.floating[0]?.h], [300, 200, 500, MIN_FLOAT.h]);
  l = place(l, "project", { kind: "tab", stack: f?.id ?? "" });                    // another panel joins the window's tabs
  assert.deepEqual(l.floating[0]?.panels, ["elements", "project"]);
  assert.throws(() => place(l, "properties", { kind: "split", stack: f?.id ?? "", edge: "left" }), DockError);   // a window holds tabs only
  l = place(l, "elements", { kind: "tab", stack: stackOf(l, "properties").id });
  sound(l);
  assert.equal(sideOf(l, "elements"), "right");
  assert.deepEqual(l.floating[0]?.panels, ["project"]);
  l = place(l, "project", { kind: "tab", stack: stackOf(l, "properties").id });
  assert.equal(l.floating.length, 0);                                              // an emptied window closes itself
});

test("a tab can be dragged out of a combined panel into a window, or into another dock", () => {
  const l = fresh();
  const out = place(l, "elements", { kind: "float", x: 40, y: 40 });
  assert.deepEqual(stackOf(out, "project").panels, ["project"]);
  const moved = place(l, "elements", { kind: "region", side: "bottom" });
  assert.equal(sideOf(moved, "elements"), "bottom");
  sound(moved);
});

test("closing a panel keeps it reopenable, and reopening puts it back where it was", () => {
  const l = fresh();
  const closed = close(l, "elements");
  sound(closed);
  assert.deepEqual(closed.hidden, ["elements"]);
  assert.deepEqual(stackOf(closed, "project").panels, ["project"]);
  const back = reopen(closed, "elements", PANELS);
  sound(back);
  assert.deepEqual(stackOf(back, "project").panels, ["project", "elements"]);
  assert.equal(stackOf(back, "project").active, "elements");
  assert.deepEqual(back.hidden, []);
  const moved = close(place(l, "properties", { kind: "region", side: "bottom" }), "properties");
  assert.equal(moved.regions.right.root, null);
  const again = reopen(moved, "properties", PANELS);
  assert.equal(sideOf(again, "properties"), "bottom");                              // the dock it was last in, even though that dock is now empty
  const everything = PANELS.reduce((acc, p) => close(acc, p.id), l);               // any panel, even all of them, can be closed and brought back
  sound(everything);
  assert.equal(allPanels(everything).length, 3);
  assert.deepEqual(PANELS.reduce((acc, p) => reopen(acc, p.id, PANELS), everything).hidden, []);
  assert.equal(close(closed, "elements"), closed);
});

test("resetting a panel sends it back to its home dock whatever happened to it", () => {
  let l = place(fresh(), "elements", { kind: "float", x: 5, y: 5 });
  l = resetPanel(l, "elements", PANELS);
  sound(l);
  assert.deepEqual(stackOf(l, "project").panels, ["project", "elements"]);
  l = toggleRegion(close(l, "properties"), "left");
  l = resetPanel(l, "properties", PANELS);
  assert.equal(sideOf(l, "properties"), "right");
  assert.equal(l.regions.right.collapsed, false);
});

test("what cannot be done is refused with a reason, and changes nothing", () => {
  const l = fresh();
  const own = stackOf(l, "properties").id;
  assert.equal(place(l, "properties", { kind: "tab", stack: own }), l);              // dropping a panel on its own tab is nothing
  assert.throws(() => place(l, "properties", { kind: "split", stack: own, edge: "left" }), /itself/);
  assert.throws(() => place(l, "nope", { kind: "region", side: "left" }), /no panel/);
  assert.throws(() => place(l, "project", { kind: "tab", stack: "gone" }), /no longer there/);
  assert.throws(() => place(l, "project", { kind: "float", x: Number.NaN, y: 0 }), DockError);
  assert.throws(() => resizeSplit(l, "gone", 0, 0.1), DockError);
  assert.throws(() => setRegionSize(l, "left", Number.NaN), DockError);
});

test("dragging a divider never squeezes a pane out, and a dock never gets thinner than its minimum", () => {
  const l = place(fresh(), "elements", { kind: "split", stack: stackOf(fresh(), "properties").id, edge: "bottom" });
  const split = l.regions.right.root;
  assert.equal(split?.kind, "split");
  const id = split?.id ?? "";
  const a = resizeSplit(l, id, 0, 0.2);
  assert.deepEqual((a.regions.right.root as { weights: number[] }).weights.map((w) => Math.round(w * 100) / 100), [0.7, 0.3]);
  const b = resizeSplit(l, id, 0, 9);
  assert.ok(((b.regions.right.root as { weights: number[] }).weights[1] ?? 0) >= MIN_WEIGHT - 1e-12);
  const c = resizeSplit(l, id, 0, -9);
  assert.ok(((c.regions.right.root as { weights: number[] }).weights[0] ?? 0) >= MIN_WEIGHT - 1e-12);
  assert.equal(setRegionSize(l, "left", 10).regions.left.size, MIN_REGION);
  assert.throws(() => resizeSplit(l, id, 5, 0.1), DockError);
});

test("however the docks are arranged, the modelling view keeps its room", () => {
  let l = fresh();
  l = setRegionSize(setRegionSize(l, "left", 700), "right", 700);
  const narrow = fit(l, { width: 1000, height: 700 });
  const sizes = regionSizes(narrow);
  assert.ok(1000 - sizes.left - sizes.right >= MIN_CENTER);
  assert.ok(narrow.regions.left.size >= MIN_REGION && narrow.regions.right.size >= MIN_REGION);
  const tiny = fit(l, { width: 300, height: 200 });
  assert.ok(tiny.regions.left.size >= MIN_REGION);                                   // it cannot do more than the minimums, and says so by not going below them
  const wide = fit(setRegionSize(fresh(), "left", 300), VIEW);
  assert.equal(wide.regions.left.size, 300);                                         // a layout that fits is left alone
  const collapsed = toggleRegion(l, "left");
  assert.ok(regionSizes(collapsed).left < MIN_REGION);                               // a collapsed dock is a thin strip that can be reopened
  assert.equal(regionSizes(fresh()).top, 0);                                          // an empty dock takes no room
});

test("a floating panel is always kept on a screen that exists, including after a monitor is unplugged", () => {
  const l = place(fresh(), "elements", { kind: "float", x: 2400, y: 100, w: 300, h: 300 });
  const both = [{ x: 0, y: 0, w: 1920, h: 1080 }, { x: 1920, y: 0, w: 1920, h: 1080 }];
  assert.equal(fit(l, VIEW, both).floating[0]?.x, 2400);                              // on the second monitor and it is still there: left alone
  const gone = fit(l, VIEW, [both[0] as { x: number; y: number; w: number; h: number }]);
  const f = gone.floating[0];
  assert.ok((f?.x ?? -1) >= 0 && (f?.x ?? 9999) + (f?.w ?? 0) <= 1920);
  const off = fit(place(fresh(), "elements", { kind: "float", x: -500, y: -500 }), VIEW);
  assert.ok((off.floating[0]?.x ?? -1) >= 0 && (off.floating[0]?.y ?? -1) >= 0);
});

test("a saved layout that is damaged is repaired, and what changed is reported", () => {
  const good = validate(JSON.parse(serialize(fresh())), PANELS);
  assert.deepEqual(good.repairs, []);
  sound(good.layout);
  for (const raw of [null, undefined, 7, "x", [], {}, { version: 2 }, { version: 1 }, { version: 1, regions: 3 }]) {
    const r = validate(raw, PANELS);
    sound(r.layout);
    assert.deepEqual(stackOf(r.layout, "project").panels, ["project", "elements"]);   // anything unusable gives the default
  }
  assert.deepEqual(validate(null, PANELS).repairs, []);                               // nothing saved yet is not a repair
  assert.equal(validate({ version: 2 }, PANELS).repairs.length, 1);

  const base = JSON.parse(serialize(fresh())) as Layout;
  const unknown = JSON.parse(JSON.stringify(base)) as Layout;
  (unknown.regions.left.root as Stack).panels.push("plugin-gone");
  const a = validate(unknown, PANELS);
  sound(a.layout);
  assert.ok(!allPanels(a.layout).includes("plugin-gone"));                            // a panel that no longer exists is dropped

  const missing = JSON.parse(JSON.stringify(base)) as Layout;
  (missing.regions.left.root as Stack).panels = ["project"];
  const b = validate(missing, PANELS);
  sound(b.layout);
  assert.equal(sideOf(b.layout, "elements"), "left");                                 // a panel the file lacks (a new one) is brought in
  assert.equal(b.repairs.length, 1);

  const dup = JSON.parse(JSON.stringify(base)) as Layout;
  (dup.regions.right.root as Stack).panels.push("project");
  const c = validate(dup, PANELS);
  sound(c.layout);
  assert.equal(allPanels(c.layout).filter((p) => p === "project").length, 1);        // one place only

  const broken = { version: 1, regions: { left: { size: -5, collapsed: "yes", root: { kind: "split", dir: "row", weights: ["a", -1], children: [{ kind: "stack", panels: [], active: "" }, { kind: "stack", panels: ["project", "project"], active: "zzz" }] } },
    right: { size: 400, root: { kind: "nonsense" } }, top: null, bottom: { size: 200, collapsed: false, root: { kind: "stack", panels: ["properties"], active: "properties" } } },
    floating: [{ panels: ["elements"], x: "left", y: NaN, w: -4, h: 1e9 }, { panels: [] }, null], hidden: ["elements", "x", 4], from: { elements: "middle", project: "left" } };
  const d = validate(broken, PANELS, VIEW);
  sound(d.layout);
  assert.equal(d.layout.regions.left.size >= MIN_REGION, true);
  assert.equal(d.layout.regions.left.collapsed, false);
  assert.ok(d.repairs.length >= 1);
  assert.deepEqual(d.layout.from, { project: "left" });
});

test("a split nested absurdly deep, or a cycle of JSON, cannot hang or crash the recovery", () => {
  let node: unknown = { kind: "stack", panels: ["project"], active: "project" };
  for (let i = 0; i < 200; i++) node = { kind: "split", dir: "row", weights: [1, 1], children: [node, { kind: "stack", panels: [], active: "" }] };
  const r = validate({ version: 1, regions: { left: { size: 300, collapsed: false, root: node }, right: { size: 300, collapsed: false, root: null }, top: { size: 160, collapsed: false, root: null }, bottom: { size: 160, collapsed: false, root: null } }, floating: [], hidden: [], from: {}, seq: 0 }, PANELS);
  sound(r.layout);
  assert.equal(parse("{not json", PANELS).repairs.length, 1);
  assert.equal(parse(null, PANELS).repairs.length, 0);
});

test("the workspace is remembered across restarts, apart from any project, and named layouts can be saved and loaded", () => {
  const storage = new Memory();
  const first = new WorkspaceStore(storage, () => PANELS);
  assert.deepEqual(first.current().layout, fresh());                                  // nothing saved: the default
  const mine = place(place(fresh(), "elements", { kind: "float", x: 50, y: 60, w: 400, h: 300 }), "project", { kind: "tab", stack: stackOf(fresh(), "properties").id });
  first.save(mine);
  const restarted = new WorkspaceStore(storage, () => PANELS);                        // a new run of the app reads the same storage
  assert.equal(frozen(restarted.current().layout), frozen(mine));
  assert.ok(storage.data.has(CURRENT_KEY));
  first.saveNamed("Modelling", mine);
  first.saveNamed("Drawing", fresh());
  assert.deepEqual(first.names(), ["Drawing", "Modelling"]);
  assert.equal(frozen(first.loadNamed("Modelling").layout), frozen(mine));
  assert.deepEqual(restarted.restoreDefault(), fresh());
  assert.equal(frozen(restarted.current().layout), frozen(fresh()));
  first.removeNamed("Drawing");
  assert.deepEqual(first.names(), ["Modelling"]);
  assert.throws(() => first.loadNamed("Drawing"), /no saved workspace/);
  assert.throws(() => first.saveNamed("  ", mine), DockError);
  assert.ok([...storage.data.keys()].every((k) => k === CURRENT_KEY || k.startsWith(NAMED_PREFIX)));   // only workspace keys: nothing of a project is written
});

test("a workspace store with no storage, or storage that throws, still works for the session", () => {
  const throwing: KeyStore = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("full"); }, removeItem() { throw new Error("denied"); } };
  for (const s of [null, throwing]) {
    const store = new WorkspaceStore(s, () => PANELS);
    assert.deepEqual(store.current().layout, fresh());
    store.save(fresh());
    store.saveNamed("x", fresh());
    assert.deepEqual(store.names(), []);
  }
});

test("a saved layout meets a changed set of panels (a plug-in added or removed) without losing the arrangement", () => {
  const store = new WorkspaceStore(new Memory(), () => PANELS);
  store.save(place(fresh(), "properties", { kind: "tab", stack: stackOf(fresh(), "project").id }));
  const more: PanelDef[] = [...PANELS, { id: "plugin", title: "Plug-in", home: "bottom" }];
  const grown = new WorkspaceStore((store as unknown as { storage: KeyStore }).storage, () => more).current();
  sound(grown.layout, more);
  assert.equal(sideOf(grown.layout, "plugin"), "bottom");
  assert.equal(sideOf(grown.layout, "properties"), "left");                           // the person's arrangement of the others is kept
  const fewer = new WorkspaceStore((store as unknown as { storage: KeyStore }).storage, () => PANELS.slice(0, 2)).current();
  sound(fewer.layout, PANELS.slice(0, 2));
});

// A seeded random walk over every operation: whatever happens, the layout stays sound and only DockErrors are thrown.
function rng(seed: number): () => number { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; }; }

test("500 random operations in a row never produce an unsound layout", () => {
  const many: PanelDef[] = [...PANELS, { id: "a", title: "A", home: "bottom" }, { id: "b", title: "B", home: "top" }, { id: "c", title: "C", home: "right", order: 1 }];
  for (const seed of [1, 7, 42, 2026]) {
    const r = rng(seed);
    const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(r() * xs.length)] as T;
    let l = defaultLayout(many);
    for (let i = 0; i < 500; i++) {
      const panel = pick(many).id;
      const stacks = [...SIDES.flatMap((s) => stacksOf(l.regions[s].root)), ...l.floating];
      const target: DropTarget = pick<DropTarget>([
        { kind: "tab", stack: stacks.length ? pick(stacks).id : "none", index: Math.floor(r() * 4) },
        { kind: "split", stack: stacks.length ? pick(stacks).id : "none", edge: pick(SIDES) },
        { kind: "region", side: pick(SIDES) },
        { kind: "float", x: Math.floor(r() * 2000) - 100, y: Math.floor(r() * 1200) - 100 },
      ]);
      try {
        const op = Math.floor(r() * 10);
        if (op < 5) l = place(l, panel, target);
        else if (op === 5) l = close(l, panel);
        else if (op === 6) l = reopen(l, panel, many);
        else if (op === 7) l = resetPanel(l, panel, many);
        else if (op === 8) { const s = stacks.length ? pick(stacks) : null; if (s) l = activate(l, s.id, pick(s.panels)); }
        else l = fit(toggleRegion(l, pick(SIDES)), { width: 600 + Math.floor(r() * 1400), height: 400 + Math.floor(r() * 600) });
      } catch (error) {
        assert.ok(error instanceof DockError, String(error));
      }
      assert.deepEqual(problems(l, many), [], `seed ${seed}, step ${i}`);
      assert.equal(findStack(l, "nope"), null);
      const again = validate(JSON.parse(serialize(l)) as unknown, many).layout;      // and it survives being stored and read back unchanged
      assert.deepEqual(problems(again, many), []);
      assert.deepEqual(allPanels(again).sort(), allPanels(l).sort());
    }
  }
});
