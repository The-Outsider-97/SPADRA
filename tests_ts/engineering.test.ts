import { test } from "node:test";
import assert from "node:assert/strict";
import { resultRows } from "../src/client/engineering.js";
import type { BeamAnalysis } from "../src/client/types.js";

const answer = (over: Partial<BeamAnalysis["outputs"]> = {}): BeamAnalysis => ({
  id: "b", name: "Beam", agent: "SLAI STEM Agent", method: "m", note: "n", validation: "analytical_baseline", revision: 1,
  inputs: { span_m: 6, width_m: 0.2, depth_m: 0.4, material: "Steel", support: "simply_supported", load_type: "udl", load: 10, load_unit: "kN/m", youngs_modulus_gpa: 200, youngs_modulus_source: "x", second_moment_m4: 0.00106667, section_modulus_m3: 0.00533 },
  outputs: { max_moment_nm: { value: 45000, unit: "N*m" }, max_shear_n: { value: 30000, unit: "N" }, max_deflection_m: { value: 0.0079101, unit: "m" }, deflection_limit_m: { value: 0.016667, unit: "m" }, ...over },
  assumptions: [], details: { base_engineering_check: { demand: 0.0079101, limit: 0.016667, utilization: 0.4746, passed: true } },
});

test("the agent's SI results are shown in kN·m, kN and mm without changing their value", () => {
  const rows = new Map(resultRows(answer()));
  assert.equal(rows.get("Maximum bending moment"), "45 kN·m");
  assert.equal(rows.get("Maximum shear force"), "30 kN");
  assert.equal(rows.get("Maximum deflection"), "7.91 mm");
  assert.equal(rows.get("Engine deflection limit"), "16.67 mm");
  assert.equal(rows.get("Engine deflection check"), "47.5 % of the limit: within");
});

test("a result the agent did not provide is not shown", () => {
  const a = answer(); delete a.outputs["deflection_limit_m"]; delete a.details.base_engineering_check;
  const labels = resultRows(a).map(([k]) => k);
  assert.deepEqual(labels, ["Maximum bending moment", "Maximum shear force", "Maximum deflection"]);
});
