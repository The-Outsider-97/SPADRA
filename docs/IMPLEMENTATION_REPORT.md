# Parametric modelling: implementation report

Branch `feature/parametric-modeling` in `applications/SPADRA` (base `main` 7ea9b98). Nothing was merged or pushed. All timings are from a Linux VM (2 cores,
Python 3.10), not the Windows desktop. The section letters A–H follow the order of the request as I could reconstruct it; the original lettering was not recoverable.

## A. What was built
Typed parameters, family types, formula engine and unit-aware dependency graph with incremental evaluation; transactional commands with rollback and undo/redo;
sandboxed Python scripting; procedural stairs, façade and structural frame; 2D/3D constraint engine (DOF, conflicts, redundancy); deterministic optimizer and
alternatives; hinged-door swing animation with obstruction check; agent proposals (agents propose, the user approves, a transaction commits); professional UI panels;
SGEO/SDRW/SLDC integration and migration; interop report; docs. Second pass (report pp. 11–15 and 26–34, read afterwards): door kinematics with a measured
motion check, time-dependent motion laws, a deterministic requirement-in-words → family draft, L/U stairs, running-bond and rhythm façades, parametric family
schedules (also in the SLDC project report) and plan-level constraints between walls, beams and columns. Each is described in section I. Details and routes: `docs/PARAMETRIC_MODELING.md`.

## B. Rules kept
Deterministic modelling does not depend on AI. Script, optimizer, alternatives and agent output become proposals (dry-run on a scratch copy → consent → stale-revision
check → commit, one undo step each). No learned or predictive feature was faked.

## C. Tests
Unit and integration tests were added on both sides (families, parameters, scripting, procedural, optimization, proposals, interop, door swing, alternatives, one
end-to-end scenario with save/reopen and full undo, constraint analysis in the SLAI agent, parameter-graph cache). The last full `tests/spatial_formats` run (second pass): 592 passed,
18 failed, 2 errors, 26 skipped; every failure is a missing package here (`trimesh`, `scipy`, provenance dependencies), none is an assertion. The third pass (section K) added 8 SPADRA test
modules and a parameter-cache test on the SLAI side; the SLAI parameter/optimizer/constraint tests were re-run (198 passed).

## D. Benchmarks
`tests/benchmark_parametric.py`, results in `docs/parametric_benchmarks.json` (regenerated in the third pass; the machine, Python version and CPU count are recorded in the file; a 2-core Linux VM, not the Windows desktop).
Protocol (report §7): per scale it records setup, first edit (cold) and later edits (warm) as p50/p95/min/max, the same change done as a **full rebuild** for comparison, failures, and peak RSS.
Measured: 128 parameters evaluate in ~3.7 ms full / ~0.9 ms incremental; a script run costs ~65 ms warm (OS-sandboxed); a 169-panel façade takes ~3.4 s to generate and ~6.7 s to regenerate when its wall moves
(unchanged: that path was not optimised).
Dependent instances, editing a type (incremental) vs rebuilding all instances from scratch (p50): 1 → 1.4 ms vs 0.9 ms (incremental is *slower* at one instance); 10 → 8 ms vs 25 ms; 100 → 62 ms vs 1.77 s; 300 → 206 ms vs 16.6 s.
Placing instances is still quadratic (the SGEO scene is validated on every `apply`): 100 instances took 1.7 s, 300 about 16.5 s. **1000 instances were not measured**: the setup alone is projected far beyond the time budget, and the table says
`measured: false` for it rather than extrapolating. Earlier number for comparison: updating a type with 100 instances took ~5.5 s before the batching in section K and ~0.06 s after.

## E. Regression safety
Third pass, full SPADRA run (`unittest discover`, Linux VM): 1582 tests, 3 failures, 7 skipped. Two are environmental and identical on the baseline: `test_api.ModifierPreview` (no `trimesh`) and `test_startup` build record
(`dist/` is stale: **run `npm run build`**). The third failure was one of my own tests patching a function I had since replaced (`FM.regenerate`); it now patches `FM._placement` and passes. Earlier known items still hold: `test_forms`
(no `trimesh`), `test_bundle`/`test_utils` (old `jsonschema` in subprocesses), `test_reports` (your uncommitted edit vs SLDC 1.1.0), `test_jobs` (latent race in unmodified `jobs.py`). TypeScript: `tsc --noEmit` clean, 130/130 `tsx` unit tests.
These runs were on the cloud copy of the tree, not on your Windows machine.

## F. Git state
SPADRA: 10 commits on `feature/parametric-modeling` (HEAD 78ea150). Your unrelated local changes (dist/*, cutting.py, forms.py, some tests, deletions under .github, assets,
docs) were left uncommitted on purpose; local SPADRA also differs from remote `main` in files I did not touch. SLAI: **my changes there are uncommitted** (`src/agents/spatial_agent.py`
`analyze_constraints`, `src/utils/spatial_formats/*` incl. `parameters.py` cache, optimizer/alternatives, `tests/spatial_formats`, `tests/agents`) because those directories are untracked
and mixed with your own uncommitted work; committing selectively would have rewritten your history. No branch was created there.

## I. Second pass: what the report's pp. 11–15 and 26–34 added
Every item below has tests that read the result back from the stored model (not from the generator's own bookkeeping). Test counts are from the last run here.
- **Door kinematics** (`door_swing.py`, 26 tests): the leaf turns on a smoothstep law; `verify_motion` samples SGEO's *evaluated* animation and checks the hinge stays fixed,
  the angle is monotone and reaches the target, and the leaf stays on its arc. `model.resize_door` keeps the hinge (or the centre) where it was and re-aims the animation.
  A preset defect was found by these tests (an automatic tangent overshot when the start angle was non-zero) and fixed with explicit zero tangents (`ease_between`).
- **Motion laws** (`motion_laws.py`, command `anim.law`): linear and sinusoid laws are authored as keys with analytic tangents; the cubic-Hermite error is measured against the exact
  law and the command is refused and rolled back beyond the tolerance.
- **Requirement in words → family** (`family_nl.py`, 23 tests): a deterministic pattern interpreter (no language model) for windows, doors and beams with units. It returns
  a typed draft with a preview; nothing changes until `POST /api/families/draft/propose` produces a proposal that goes through the usual dry-run, consent and revision check.
  Statuses are `draft`, `needs_input`, `invalid`, `unsupported`; unknown wording is said to be unsupported rather than guessed.
- **L and U stairs** (`stairs.py`, 16 tests): two flights and one landing of depth max(landing depth, width), flights and landing as floors, same headroom check.
- **Façade patterns** (`procedural.py`, 19 tests): `grid`, `running_bond` (offset, cut end panels) and `rhythm` (listed widths; a sliver at the end is folded in). Panels are cut
  around openings, ids stay stable, switching patterns and undo are tested; bad input is refused with nothing created.
- **Parametric schedules** (`family_schedule.py`, 19 tests): one schedule per family; columns are the family's typed parameters in their units; each cell is SGEO's resolved value
  with its source (instance / type / default / formula); totals only for areas, volumes and masses. It appears in the browser, on sheets, in the CSV and in the SLDC project report,
  and survives save/reopen.
- **Model constraints** (`model_constraints.py`, 16 tests): coincident, distance, fixed, parallel, perpendicular, equal length, angle, horizontal, vertical, point-on-line between walls,
  beams and columns in plan, stored in the project manifest. `analyze` reports degrees of freedom, redundant and conflicting relations and unresolved references using the
  neutral constraint engine; `solve` applies the result through ordinary commands, re-measures it, and rolls back if a relation is not met.

## J. Field fixes after the Windows run
- **Existing SDRW profile for frames.** The chooser listed only polylines whose `closed` flag was true, but a polyline finished by clicking its first point again (then Enter) was stored open with a repeated
  start point, so a clearly closed loop was reported as "No closed SDRW polyline is available". Now (a) the polyline tool closes the loop when the first point is clicked, (b) `draw.polyline`
  stores a looped polyline as closed, (c) the chooser (`closedPolylinePoints`, `openingProfileSources`) and the server (`_opening_profile_section`) accept either form. Tests: `test_door_panels.py`,
  `geom2d.test.ts`, `profiles.e2e.test.mjs` (real browser).
- **Dimensions follow wall layers** (`dimension_links.py`, 7 tests in `test_dimension_links.py` + an e2e). An aligned dimension point placed on a wall face or axis (within 1 mm) is remembered as
  wall + fraction along + face. Any model command (layer added, thickness, stretch, move) re-evaluates it in the same transaction, so one Undo restores wall and dimension together. A point not on a wall is never
  linked; dragging a grip off the wall releases the link; a deleted wall leaves the dimension where it was.

## K. Third pass: the report's quality gates (Table 16) and the remaining items
- **P0-01 versioned family contract** (`tests/test_family_contract.py`, 10). A family is exported as a native SGEO *family file* and can now be **imported** (`model.import`, one revision, one undo step, renamed on a name
  clash, a repeat adds nothing). Files written by SGEO 0.1.0 and 0.2.0 are upgraded in memory and the report says so. Invalid types, units, bounds, formula dimensions and tampered files are refused with the project's full fingerprint unchanged.
  Bugs found by writing it: the family file could be exported but never read back; native import dropped family types and an instance's `family_type`; the parse cache lost the source version. All fixed.
- **P0-03 fault at every stage** (`test_fault_injection.py`, 5): a fault injected *after* each reconciler has done its work (foundations, arrays, façades, voids, attachments, rooms, tags, linked dimensions) and inside the handler leaves model,
  drawings, manifest, revision and history exactly as before; the retry then succeeds. Found: a linked dimension was snapshotted twice, so rollback/undo of a command that moved it restored the post-command state (`Project.touch` now skips the running document).
- **P0-05 undo chains** (`test_undo_chains.py`, 4): a 10-command chain (dimension, placements, type edits, layer, property, move) is undone and redone step by step; each step returns to the exact state it was made from; a new edit mid-history clears redo; commit receipts survive undo.
- **P1 scripts** (`test_script_records.py`, 13): run records carry hashes, interpreter and environment identity, inputs and seed; `POST /api/scripts/replay` re-runs a record and reports `identical` or the first difference; permissions are checked per version.
  The report's malicious-import, path, symlink, loop, allocation and NaN cases are exercised on Linux only (section G).
- **P1 optimization study** (`test_optimization_study.py`, 8): every status (converged, budget_exhausted, infeasible, failed, timeout) is reported and none is shown as success; min > max is a clean 400 (it leaked a validation error before). `unbounded` cannot arise through the family
  path because the parameter engine caps magnitudes at 1e12; it is covered only at the SLAI optimizer level.
- **P1 proposal lifecycle** (`test_proposal_lifecycle.py`, 10): pending, stale, rejected, cancelled, executed, failed; consent and revision binding.
- **P2 prototype round trip and interop fixtures** (`test_prototype_roundtrip.py`, 6; `test_interop_fixtures.py`, 5): save/reopen/export/import keeps families, types and instances; export reports list what is lost (animation is now named for non-SGEO formats).
- **Performance** (`families.regenerate_many`, `BATCH = 500`; `place` as one transaction; parameter-result memo in SLAI `parameters.py`): see section D. Behaviour is unchanged; the fault and undo tests above run over the batched path.
- **Not done, by design or by limit**: P3 items (experimental in the report: learned or predictive generation) are not implemented; Windows script isolation; stale/ambiguous reference states beyond plan level are not reachable by id in SPADRA, so they are not tested at the app level;
  the structural-assembly edge-bay policy (§6.5) is not implemented; placement is still quadratic.

## G. Known limits (not hidden)
- Windows script isolation is not implemented or verified: there only hash-approved versions run (`process-isolated`), untrusted execution stays disabled and the UI says so.
- Agents needing scipy/torch/joblib/pyarrow (STEM, Optimization, Planning, Provenance, Language) could not run here; no enhancements to Language, Planning, Verification, Orchestrator or Simulation agents.
- Predictive/learned features: not implemented. SLDC parametric *dimensions* (dimensions driven by parameters) are not implemented; parametric schedules are.
- Constraints between model elements are plan-level (walls, beams, columns); solids in space, elevations and sections are not constrained, and the 3D engine is still not wired
  to scenes. Stairs are straight, L or U (two flights and one landing, no wall-clearance check); headroom uses vertical rays; door swing is single-leaf, AABB-based.
- The words-to-family interpreter only understands window, door and beam requirements in English; anything else comes back `unsupported`.
- The client UI was not extended for the second pass (templates, lights, workflow, beam search, word drafts, family schedules, constraints, L/U stairs, façade patterns are
  reachable through commands and routes only). The TypeScript client could not be tested here, and `dist/` is stale until `npm run build`.
- Script `run` holds the project lock while it executes. Client unit tests (tsx) and DXF round-trip could not be run here.
- The report's end-to-end workflows A–H were not recoverable from the pages available; the second pass used the report's own worked scenarios instead.
- Placing many instances is still quadratic (300 instances ~16 s; 1000 not measurable here). Editing them is fast (section D). About 4000 façade panels took ~95 s to generate, and the façade command refuses beyond a column limit.
- The structural-assembly edge-bay policy (report §6.5) and the Table 16 P3 items are not implemented.

## H. Acceptance matrix
| Requirement | Status | Evidence |
|---|---|---|
| Typed parameters, family types, formulas, dependency graph, incremental eval | Complete | tests/spatial_formats/test_parameters*.py, test_family_types.py; benchmark |
| Transactions, rollback, undo/redo | Complete | test_parametric_workflow (full undo), existing project tests |
| Secure scripting, Linux | Complete | scripting tests; isolation self-test reports `os-sandboxed` |
| Secure scripting, Windows | Not implemented | approved-hash execution only; stated in UI and docs |
| Procedural stairs / façade / frame | Complete for its stated scope | tests/test_procedural.py (12), test_stair_turning.py (16), test_facade_patterns.py (19); straight/L/U stairs, grid/running-bond/rhythm façade; limits in G |
| Constraints with DOF and stable references | Partial | engine + 2D tests; plan-level wall/beam/column constraints with solve and rollback (test_model_constraints.py, 16); not solids in space, 3D not wired to scenes |
| Agent enhancements (propose only) | Partial | Spatial agent `analyze_constraints` + proposals tests; other agents not changed; SLAI side uncommitted |
| Deterministic alternatives / optimizer | Complete | tests/spatial_formats/test_alternatives.py, test_optimization.py; `proved: False` stated |
| Learned / predictive generation | Not implemented | by design: no fabricated ML |
| Parametric animation (hinged door, motion laws) | Complete for single-leaf doors | test_door_swing.py (12), test_door_kinematics.py (26): measured motion check, hinge-anchored resize, linear/sinusoid laws with measured error |
| Requirement in words → typed family | Partial | test_family_nl.py (23); deterministic, window/door/beam only, proposal-gated |
| UI | Partial | `parametric_ui.ts` builds in a scratch copy; not extended for the second pass; client unit tests not run, not visually exercised |
| SGEO/SDRW/SLDC integration, migration | Partial | interop tests; test_family_schedule.py (19) incl. SLDC project report; parametric dimensions missing |
| Interop docs (preserved/approximated/dropped) | Complete | `docs/PARAMETRIC_MODELING.md`; export reports list lost data; DXF unverified |
| Benchmarks recorded | Complete for 1–300 instances | `docs/parametric_benchmarks.json` (VM numbers, with full-rebuild comparison); 1000 not measured |
| Regression safety | Partial | third-pass full run: 1582 tests, 2 environmental failures (section E) plus the older classified modules; not run on Windows |
| Quality gates P0-01/03/05, P1 scripts/optimization/proposals, P2 round trip (Table 16) | Complete on Linux for the stated cases | test_family_contract, test_fault_injection, test_undo_chains, test_script_records, test_optimization_study, test_proposal_lifecycle, test_prototype_roundtrip, test_interop_fixtures; limits in K and G |
| End-to-end workflows A–H | Partial | one integrated scenario; mapping to A–H unconfirmed |
| Git delivery | Partial | SPADRA committed locally, SLAI uncommitted; no push |
