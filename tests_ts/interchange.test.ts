import assert from "node:assert/strict";
import { test } from "node:test";
import { extensionOf, formatsFor, reportSections, statusText } from "../src/client/interchange.js";
import { editCommand } from "../src/client/properties.js";
import { ancestors, iconFor } from "../src/client/browser.js";
import { progressText } from "../src/client/jobs_ui.js";
import type { ConversionReport, Job, TreeNode } from "../src/client/types.js";

const report = (over: Partial<ConversionReport> = {}): ConversionReport => ({ direction: "import", status: "partial", converted: { objects: 3, meshes: 2 }, lost: ["materials"], approximated: [], unsupported: ["curved faces"], errors: [], warnings: ["unit assumed"], assumptions: [], ...over } as ConversionReport);

test("a conversion report lists what was kept, then what was not, leaving out empty lists", () => {
  const s = reportSections(report());
  assert.deepEqual(s.map((x) => x.title), ["Carried over", "Not carried over", "Not supported", "Warnings"]);
  assert.deepEqual(s[0]?.items, ["Objects: 3", "Meshes: 2"]);
  assert.deepEqual(reportSections(report({ converted: {}, lost: [], unsupported: [], warnings: [] })), []);
});

test("the status line says plainly whether anything was lost", () => {
  assert.match(statusText(report({ status: "complete" })), /Everything/);
  assert.match(statusText(report({ status: "partial" })), /could not be carried over/);
  assert.match(statusText(report({ status: "failed" })), /did not complete/);
});

test("the extension is the last part of the name, lower-cased", () => {
  assert.equal(extensionOf("Plan.Final.DXF"), "dxf");
  assert.equal(extensionOf("noext"), "noext");
});

test("formats come in the order they are offered, and an installation that lacks a library says why", () => {
  const store = { interop: { importModel: { sgeo: { available: true }, glb: { available: true }, stl: { available: true }, obj: { available: true }, ifc: { available: false, reason: "ifcopenshell is not installed" } },
    exportModel: {}, importDrawing: {}, exportDrawing: { dxf: { available: false, reason: "ezdxf is not installed" } } } } as never;
  const imp = formatsFor(store, "model", "import");
  assert.deepEqual(imp.map((f) => f.id), ["sgeo", "glb", "stl", "obj", "ifc"]);
  assert.equal(imp[4]?.state.available, false);
  assert.deepEqual(formatsFor(store, "model", "export").map((f) => f.id), ["sgeo", "glb", "stl", "obj"]);
  const dxf = formatsFor(store, "drawing", "export").find((f) => f.id === "dxf");
  assert.equal(dxf?.state.available, false);
  assert.equal(formatsFor({ interop: null } as never, "drawing", "import").every((f) => f.state.available), true);
});

test("every editable row maps to one command with the parameters the server expects", () => {
  const model = { kind: "model", ref: { model: "w1" } } as never;
  assert.deepEqual(editCommand(model, "width", 200), { op: "model.set_property", params: { id: "w1", key: "width", value: 200 } });
  assert.equal(editCommand(model, "custom:Fire rating", "60")?.op, "model.set_property");
  const sheet = { kind: "sheet", ref: { sheet: "s1" } } as never;
  assert.deepEqual(editCommand(sheet, "vp:v9:scale", "1:50"), { op: "sheet.set_viewport", params: { id: "s1", viewport: "v9", key: "scale", value: "1:50" } });
  assert.deepEqual(editCommand(sheet, "note:n3", "Hello"), { op: "sheet.set_note", params: { id: "s1", note: "n3", key: "text", value: "Hello" } });
  assert.deepEqual(editCommand(sheet, "name", "Plan"), { op: "sheet.set", params: { id: "s1", key: "name", value: "Plan" } });
  const view = { kind: "view", ref: { view: "v1" } } as never;
  assert.equal(editCommand(view, "name", "North")?.op, "view.rename");
  assert.equal(editCommand(view, "depth", 3)?.op, "view.set_section");
  assert.equal(editCommand(view, "looking", "flip")?.op, "view.flip_section");
  assert.equal(editCommand(view, "looking", "other"), null);
  const entity = { kind: "entity", ref: { drawing: "d1", entity: "e1" } } as never;
  assert.deepEqual(editCommand(entity, "tag_field", "mark"), { op: "draw.tag_field", params: { drawing: "d1", id: "e1", field: "mark" } });
  assert.deepEqual(editCommand(entity, "layer", "A-WALL"), { op: "draw.set_layer", params: { drawing: "d1", ids: ["e1"], layer: "A-WALL" } });
  assert.equal(editCommand(entity, "colour", "red"), null);
});

const node = (id: string, extra: Partial<TreeNode> = {}): TreeNode => ({ id, label: id, ...extra } as TreeNode);

test("a row's icon is its own, else what it opens, else what it is", () => {
  assert.equal(iconFor(node("levels")), "levels");
  assert.equal(iconFor(node("r1", { report: "r1" })), "report");
  assert.equal(iconFor(node("family-f1", { dialog: "families", ref: "f1" })), "family");
  assert.equal(iconFor(node("x", { open: { kind: "sheet", id: "s" } })), "page");
  assert.equal(iconFor(node("x", { open: { kind: "drawing", id: "d" }, view_kind: "section" })), "section");
  assert.equal(iconFor(node("w1", { select: { model: "w1" } })), "wall");
  assert.equal(iconFor(node("cat-Stairs")), "stair");
});

test("the groups above a row are listed outermost first, and an unknown row has none", () => {
  const tree = [node("a", { children: [node("b", { children: [node("c")] }), node("d")] })];
  assert.deepEqual(ancestors(tree, "c"), ["a", "b"]);
  assert.deepEqual(ancestors(tree, "a"), []);
  assert.deepEqual(ancestors(tree, "zz"), []);
});

test("a running job says how far it is; a finished one says how it ended", () => {
  const job = (over: Partial<Job>): Job => ({ id: "j", kind: "export.model", label: "Export the model", status: "running", progress: 0.456, message: "Writing", created: 0, started: 0, finished: null, cancellable: true, error: null, hasFile: false, ...over });
  assert.equal(progressText(job({})), "Export the model: Writing (46%)");
  assert.equal(progressText(job({ status: "failed" })), "Export the model: failed");
  assert.equal(progressText(job({ status: "cancelled" })), "Export the model: cancelled");
});
