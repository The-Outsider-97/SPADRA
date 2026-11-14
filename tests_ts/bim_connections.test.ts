import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dragAngle, leafPoint, leafTurn, partOfFace } from "../src/client/assembly.ts";
import { CORNER_LABELS, CORNER_RANGES } from "../src/client/curtainwall.ts";
import { COMPONENT_KINDS } from "../src/client/gutters.ts";
import type { DoorHandle } from "../src/client/types.ts";

/** The client's tables are the server's: read them out of the Python so the two cannot drift apart. */
const py = (file: string): string => readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
const dict = (file: string, name: string): Array<[string, string]> => {
  const m = new RegExp(`^${name}\\s*=\\s*\\{([^}]*)\\}`, "m").exec(py(file));
  assert.ok(m, `${name} not found in ${file}`);
  return [...(m as RegExpExecArray)[1].matchAll(/"([^"]+)":\s*("([^"]*)"|\(([^)]*)\))/g)].map((x) => [x[1] as string, (x[3] ?? x[4] ?? "") as string]);
};
const src = (f: string): string => readFileSync(new URL(`../src/client/${f}`, import.meta.url), "utf8");

test("the corner types and their angle ranges are the server's", () => {
  assert.deepEqual(Object.entries(CORNER_LABELS), dict("curtainwall.py", "CORNER_TYPES"));
  for (const [key, range] of dict("curtainwall.py", "CORNER_RANGES")) assert.deepEqual(CORNER_RANGES[key], range.split(",").map((v) => Number(v)));
});

test("the downpipe component kinds are the server's, in its order", () => {
  assert.deepEqual(COMPONENT_KINDS, dict("gutters.py", "COMPONENT_KINDS"));
});

test("a picked face maps to the part whose run holds it", () => {
  const runs = [{ id: "v:start", count: 12, part: "v:start" }, { id: "cell:0,0", count: 4, part: "cell:0,0" }, { id: "k1", count: 6, part: "conn:k1" }];
  assert.equal(partOfFace(runs, 0), "v:start");
  assert.equal(partOfFace(runs, 11), "v:start");
  assert.equal(partOfFace(runs, 12), "cell:0,0");
  assert.equal(partOfFace(runs, 21), "conn:k1");
  assert.equal(partOfFace(runs, 22), null);
  assert.equal(partOfFace(runs, -1), null);
});

const handle = (over: Partial<DoorHandle> = {}): DoorHandle => ({ part: "cell:1,0", key: "panel:1:0", hinge: [1, 1, 1.2], closed: [2, 1, 1.2], free: [2, 1, 1.2], sign: 1, angle: 90, open: false, ...over });

test("a door leaf turns about its hinge the way the server drew it", () => {
  const closed = handle();
  assert.equal(leafTurn(closed), 1);
  const p = leafPoint(closed, 90);
  assert.ok(Math.abs(p[0] - 1) < 1e-12 && Math.abs(p[1] - 2) < 1e-12 && p[2] === 1.2);
  // drawn open clockwise although the hinge sign says otherwise: the drawing wins
  const cw = handle({ open: true, angle: 90, free: [1, 0, 1.2], sign: 1 });
  assert.equal(leafTurn(cw), -1);
  const q = leafPoint(cw, 90);
  assert.ok(Math.abs(q[0] - 1) < 1e-12 && Math.abs(q[1] - 0) < 1e-12);
});

test("dragging the grip gives the opening angle, kept within 5-170 degrees and snapped with Shift", () => {
  const h = handle();
  assert.equal(dragAngle(h, [1 + Math.cos(Math.PI / 3), 1 + Math.sin(Math.PI / 3)]), 60);
  assert.equal(dragAngle(h, [1 + Math.cos(Math.PI / 3), 1 + Math.sin(Math.PI / 3)], true), 60);
  assert.equal(dragAngle(h, [1 + Math.cos(0.9), 1 + Math.sin(0.9)], true), 45);           // 51.6° snaps to 45°
  assert.equal(dragAngle(h, [2, 0.9]), 5);                                                  // turned the wrong way: never closed past zero
  assert.equal(dragAngle(h, [0, 1.01]), 170);
});

test("the plan and 3D views wire the issue-sheet interactions", () => {
  const v2 = src("view2d.ts"), v3 = src("view3d.ts"), store = src("store.ts");
  assert.match(v2, /e\.key === "Enter" && this\.store\.tool === "curtainwallrun"/, "Enter finishes a run of curtain walls");
  assert.match(v2, /curtainWallDialog\(this\.store, \{ points: pts/, "a run is made from all its points");
  assert.match(v2, /\/curtainwall\/pick/, "double-click in plan picks a part");
  assert.match(v3, /addEventListener\("dblclick"[\s\S]{0,300}editPartAt/, "double-click in 3D picks a part");
  assert.match(v3, /open_to/, "the 3D door grip writes the opening angle");
  for (const label of ["Add Drip Edge", "Edit Components…", "Corner at Start…", "Stand on the Floor Below", "Add Point Drain Here…", "Add Drip Edge on This Edge"]) assert.ok(store.includes(label), label);
});

test("the single curtain wall tool keeps its two-click dialog and the run is its own tool", async () => {
  const { actionById } = await import("../src/client/shortcuts.ts");
  assert.ok(actionById("tool.curtainwall"));
  assert.ok(actionById("tool.curtainwallrun"));
  const v2 = src("view2d.ts");
  assert.match(v2, /case "curtainwall": \{[\s\S]{0,500}curtainWallDialog\(this\.store, \{ start: last, end: p, level \}\)/);
});

test("the connection check groups findings by severity and names the server's domains", async () => {
  const { bySeverity, DOMAIN_LABEL } = await import("../src/client/connections.ts");
  const m = /^DOMAINS\s*=\s*\(([^)]*)\)/m.exec(py("connections.py"));
  assert.ok(m);
  assert.deepEqual(Object.keys(DOMAIN_LABEL), [...(m as RegExpExecArray)[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]));
  const f = (id: string, severity: "error" | "warning" | "info") => ({ id, severity, domain: "roof" as const, ref: "x", message: id, check: "c", evidence: {} });
  const groups = bySeverity([f("a", "info"), f("b", "error"), f("c", "info")]);
  assert.deepEqual(groups.map(([label, list]) => [label, list.map((x) => x.id)]), [["Errors", ["b"]], ["Notes", ["a", "c"]]]);
});

test("only well-formed corrections from a server error are offered", async () => {
  const { fixesOf } = await import("../src/client/errors.ts");
  assert.deepEqual(fixesOf({ details: null }), []);
  assert.deepEqual(fixesOf({ details: { fixes: [{ label: "x", steps: [] }, { label: "y", steps: [{ op: "a", params: {} }] }] } }).map((f) => f.label), ["y"]);
});
