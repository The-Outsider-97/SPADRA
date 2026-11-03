import assert from "node:assert/strict";
import { test } from "node:test";
import { flatten, openable } from "../src/client/store.js";
import type { TreeNode } from "../src/client/types.js";

const n = (id: string, extra: Partial<TreeNode> = {}): TreeNode => ({ id, label: id, ...extra } as TreeNode);

test("a view that the Project Browser lists three times is offered once, from its first listing", () => {
  const plan = { kind: "drawing" as const, id: "p1" };
  const tree = [
    n("views", { children: [n("model", { open: { kind: "3d", id: "m" }, view_kind: "3d" }), n("p1-view", { label: "Level 01", open: plan, view_kind: "plan" })] }),
    n("models", { children: [n("model-sgeo", { open: { kind: "3d", id: "m" } })] }),
    n("drawings", { children: [n("drawing-p1", { label: "Level 01.sdrw", open: plan })] }),
    n("levels", { children: [n("lv", { label: "Level 01", open: plan })] }),
    n("scenes", { children: [n("scene-animation", { open: { kind: "3d", id: "m", timeline: true } })] }),
  ];
  assert.deepEqual(openable(tree).map((x) => x.id), ["model", "p1-view"]);
  assert.equal(flatten(tree).filter((x) => x.open?.id === "p1").length, 3);
});

test("rows that open nothing are not offered", () => {
  assert.deepEqual(openable([n("a", { children: [n("b", { select: { model: "w1" } })] })]), []);
});
