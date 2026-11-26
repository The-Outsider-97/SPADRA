import assert from "node:assert/strict";
import { test } from "node:test";
import { mm, percent, settingsPatch, visibleFindings, type Finding } from "../src/client/regulation_ui.js";

const f = (id: string, status: string, name: string): Finding => ({ id, rule: "r", title: "t", citation: "c", element: id, element_name: name, level_name: null, unit: "m", status,
  explanation: "", value: 0.8, limit: 0.85, headroom: -0.05, assumptions: [], evidence_grade: "unverified", needs: [], correctable: false, correction_note: null, dataset: { id: "d", version: "1" } });

test("the findings list puts the worst status first and the filter keeps only what was asked for", () => {
  const all = [f("a", "compliant", "Door B"), f("b", "non_compliant", "Door Z"), f("c", "approaching_limit", "Door A"), f("d", "non_compliant", "Door C")];
  assert.deepEqual(visibleFindings(all, "all").map((x) => x.id), ["d", "b", "c", "a"]);
  assert.deepEqual(visibleFindings(all, "attention").map((x) => x.id), ["d", "b", "c"]);
  assert.deepEqual(visibleFindings(all, "compliant").map((x) => x.id), ["a"]);
});

test("metres are shown as millimetres and other units as they are", () => {
  assert.equal(mm(0.792, "m"), "792 mm");
  assert.equal(mm(null, "m"), "–");
  assert.equal(mm(2.3, "m²"), "2.3 m²");
  assert.equal(percent(0.05), "5%");
});

test("the settings patch stores the margin as a fraction, sends the revision, and refuses a margin that is not a percentage", () => {
  const form = { enabled: true, jurisdiction: "NL", status: "", fn: "kantoor", date: "", marginPercent: "5", acceptUnverified: false };
  assert.deepEqual(settingsPatch(form, 7), { expected_revision: 7, enabled: true, jurisdiction: "NL", project_status: null, building_function: "kantoor", reference_date: null, warning_margin: 0.05, accept_unverified: false });
  assert.throws(() => settingsPatch({ ...form, marginPercent: "75" }, 7), /between 0 and 50/);
  assert.throws(() => settingsPatch({ ...form, marginPercent: "abc" }, 7), /between 0 and 50/);
});

test("history kinds are shown in words, an unknown kind as it is, and the detail is one short line", async () => {
  const { eventText, eventDetail } = await import("../src/client/regulation_ui.js");
  assert.equal(eventText("spadra.proposal.executed"), "Correction applied");
  assert.equal(eventText("something.new"), "something.new");
  assert.equal(eventDetail({ payload: { label: "Set width", op: "model.set_property" } }), "Set width");
  assert.equal(eventDetail({ payload: {} }), "");
  assert.equal(eventDetail({ payload: { title: "x".repeat(500) } }).length, 160);
});
