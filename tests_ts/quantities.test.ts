import { test } from "node:test";
import assert from "node:assert/strict";
import { parseDensities, rowCells, sig } from "../src/client/quantities.js";
import type { QuantityRow } from "../src/client/types.js";

const row = (over: Partial<QuantityRow> = {}): QuantityRow => ({ id: "a", name: "Basic Wall", category: "wall", material: "Concrete", volume_m3: 2.022, surface_area_m2: 23.02, size_m: [4, 0.2, 3],
  density_kg_m3: null, density_source: null, mass_kg: null, centroid_m: [2, 0, 1.5], notes: [], ...over });

test("numbers are shown to useful precision, and a missing one is a dash, not zero", () => {
  assert.equal(sig(2.0220000001), "2.022");
  assert.equal(sig(0), "0");
  assert.equal(sig(null), "–");
  assert.equal(sig(-6.4e-19), "0", "rounding noise is not shown");
  assert.equal(sig(0.000001234), "0.000001234");
  assert.equal(sig(4852.8, 5), "4852.8");
});

test("a row without a density says so instead of showing a mass", () => {
  assert.deepEqual(rowCells(row()), ["Basic Wall", "wall", "Concrete", "2.022", "23.02", "no density", "2, 0, 1.5 m"]);
  assert.equal(rowCells(row({ mass_kg: 4852.8, density_kg_m3: 2400 }))[5], "4852.8");
  assert.equal(rowCells(row({ volume_m3: null, centroid_m: null, material: null }))[3], "–");
});

test("typed densities are kept only when they are sensible numbers", () => {
  assert.deepEqual(parseDensities([["Concrete", "2400"], ["Timber", ""], ["Steel", " 7850,5 "]]), { densities: { Concrete: 2400, Steel: 7850.5 }, error: null });                    // a decimal comma is accepted, an empty box is "not given"
  assert.equal(parseDensities([["Steel", "0,5"]]).error, "The density of Steel must be a number from 1 to 25000 kg/m³.");
  assert.deepEqual(parseDensities([["Concrete", "2400"], ["Timber", "550.5"]]), { densities: { Concrete: 2400, Timber: 550.5 }, error: null });
  for (const bad of ["abc", "0", "-5", "30000", "1e9"]) assert.ok(parseDensities([["X", bad]]).error, bad);
});

import { loadDensities, quantitiesCsv } from "../src/client/quantities.js";
import type { Quantities } from "../src/client/types.js";

const sample: Quantities = {
  rows: [
    { id: "a", name: 'Wall, "north"', category: "wall", material: "Concrete", volume_m3: 2.022, surface_area_m2: 20.5, size_m: [4, 0.2, 3], density_kg_m3: 2400, density_source: "entered by you", mass_kg: 4852.8, centroid_m: [2, 0.1, -1.5], notes: [] },
    { id: "b", name: "=SUM(A1)", category: "generic", material: null, volume_m3: null, surface_area_m2: null, size_m: null, density_kg_m3: null, density_source: null, mass_kg: null, centroid_m: null, notes: ["volume: not closed", "no material"] },
  ],
  revision: 7, note: "n", source: "SGEO", needs_density: [],
  totals: { count: 2, measured: 1, volume_m3: 2.022, surface_area_m2: 20.5, mass_kg: 4852.8, mass_complete: false },
  centre: { kind: "centre of volume", point_m: [2, 0.1, -1.5], complete: false },
  by_material: [{ material: "Concrete", count: 1, volume_m3: 2.022, surface_area_m2: 20.5, mass_kg: 4852.8, mass_complete: true }],
};

test("the CSV keeps full-precision numbers, quotes text that needs it, and never lets a name act as a formula", () => {
  const lines = quantitiesCsv(sample).split("\r\n");
  assert.equal(lines[0]?.split(",").length, 15);
  assert.equal(lines[1], '"Wall, ""north""",wall,Concrete,2.022,20.5,4,0.2,3,2400,entered by you,4852.8,2,0.1,-1.5,');
  assert.equal(lines[2], '"=SUM(A1)",generic,,,,,,,,,,,,,"volume: not closed; no material"');
  assert.match(lines[3] ?? "", /^Total \(1 of 2 measured\),,,2\.022,20\.5,,,,,,4852\.8,2,0\.1,-1\.5,centre of volume$/);
  assert.ok(lines.includes("Concrete,1,2.022,20.5,4852.8"));
  assert.ok(lines.includes("Model revision 7,SGEO"));
  assert.ok(quantitiesCsv(sample).endsWith("\r\n"));
});

test("remembered densities are read defensively", () => {
  const from = (raw: string | null) => loadDensities({ getItem: () => raw });
  assert.deepEqual(from(JSON.stringify({ Concrete: "2400", Timber: "600" })), [["Concrete", "2400"], ["Timber", "600"]]);
  assert.deepEqual(from(null), []);
  assert.deepEqual(from("{not json"), []);
  assert.deepEqual(from("[1,2]"), []);
  assert.deepEqual(from(JSON.stringify({ Concrete: 2400, "": "1", Glass: "2500" })), [["Glass", "2500"]]);
});
