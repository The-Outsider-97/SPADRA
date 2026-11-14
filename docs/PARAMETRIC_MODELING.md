# Parametric modelling in SPADRA

What was added, where its data lives, what survives export, and what is not done. Every statement here was checked by running the code on a Linux VM
(Python 3.10, no scipy/trimesh, no Windows); where something could not be checked, it says so.

Design specifications, the operation graph, 2D-to-BIM reconstruction and modifier impact previews are described in [SPEC_RECONSTRUCTION_IMPACT.md](SPEC_RECONSTRUCTION_IMPACT.md).

## Features and how to reach them

| Area | Commands (all undoable, transactional) | Read-only / preview routes |
|---|---|---|
| Typed parameters, formulas, units | `family.define`, `family.update` | `POST /api/families/formula`, `GET /api/formula/functions` |
| Family types (type scope vs instance scope) | `family.type_define`, `type_duplicate`, `type_update`, `type_rename`, `type_delete`, `assign_type` | `GET /api/families` (types, dependency graph) |
| Python scripts (versioned, hash-approved) | `script.add`, `update`, `approve`, `revoke`, `delete`, `record`, `apply` | `GET /api/scripts`, `POST /api/scripts/check`, `/run`, `/propose`, `/replay` |
| Family files (native SGEO, importable) | `model.import` (format `sgeo`, a family file) | `GET /api/families/{family}/file` |
| Staircase design (straight, L, U) | `model.design_stair` (`layout`, `turn`, `gap`) | `POST /api/stairs/design`, `GET /api/stairs/{id}/headroom` |
| Facade (grid, running bond, rhythm) and frame generators | `procedural.facade` (`pattern`, `bond_offset`, `rhythm`), `frame`, `update`, `regenerate`, `delete` | `GET /api/procedural` |
| 2D geometric constraints | `draw.constraints_set`, `draw.constraints_solve` | `POST /api/drawings/constraints/analyze` |
| Constraints between walls, beams, columns (plan) | `model.constraint_add`, `constraint_delete`, `constraints_solve` | `GET /api/model-constraints`, `POST /api/model-constraints/analyze` |
| Family schedule (columns = typed parameters) | `view.add_family_schedule` | the schedule view, CSV, SLDC project report |
| Requirement in words → family draft | (proposal only) | `POST /api/families/draft`, `/api/families/draft/propose` |
| Optimization and alternatives | (proposal only) | `POST /api/optimize/family`, `/propose`, `/alternatives` |
| Hinged-door animation, resize, motion laws | `anim.swing_door`, `model.resize_door`, `anim.law` (linear, sinusoid) | `GET /api/doors/{door}/swing`, `/swing/verify` |

The UI is `src/client/parametric_ui.ts`: family types table, formula editor with dependency inspector, scripts dialog, optimization dialog (search, proposal,
alternatives), stair design preview. It only shows what the server returns and sends the commands above.

## Rules that hold everywhere

- Deterministic modelling does not depend on AI. Nothing generative is learned; "alternatives" are seeded samples and say so in their result.
- Scripts, optimizations and alternatives never change the project directly. They produce a **proposal** (`Assistant.propose_commands`): dry-run on a scratch
  copy, stale-revision check, explicit consent, then the same commands a person would run. A script's provenance (script, version, source hash, seed,
  result hash) is recorded with the change.
- A result is labelled a search or sample, never a proof (`proved: false`).

## Script isolation (what is and is not enforced)

On Linux a script runs in a child process under resource limits, `no_new_privs`, Landlock (file system and TCP) and a seccomp filter. At run time the runner
tries forbidden actions (open a file, create a socket, fork, exec) and reports `os-sandboxed` only if every one fails; only then may an unapproved script
run. Everywhere else (**Windows is not implemented and was not tested**) only a version whose exact source hash a person approved will run, and it is
labelled `process-isolated`, which is limits and a separate process, not a sandbox. The scripts dialog shows the level in force.

## Script run records and replay

A run record holds the script content hash, version, interpreter and platform identity, normalized inputs, seed, source revision and result hashes. `POST /api/scripts/replay` re-runs a record against the current project and answers `identical`
or names the first difference. A run that touches nothing nondeterministic replays identically; a script that reads time or randomness without the declared seed will not, and the replay says so. Permissions (read) are checked per version.

## Family files

`FM.export_family_file` writes a native SGEO family file (current SGEO version). Importing it (`model.import`) is one command: one revision, one undo step; a repeat adds nothing, a name clash is renamed (`Door 2`), a file written by SGEO 0.1.0 or
0.2.0 is upgraded in memory and the import report says so, and a tampered or invalid file is refused with the project unchanged.

## Where the data lives

| Data | Stored in | In an SGEO file? |
|---|---|---|
| Families, parameters, types, formulas | SGEO scene `families` | yes |
| Elements made from families, generated panels and their keys | SGEO objects and metadata | yes |
| Scripts, versions, approvals | project manifest `scripts` | **no** |
| Script provenance log | manifest `script_applications` | **no** |
| Procedural group recipes | manifest `procedural` | **no** (the generated elements stay; they stop regenerating) |
| 2D constraints | SDRW drawing | yes (SDRW) |
| Model constraints (walls, beams, columns) | manifest `model_constraints` | **no** (the solved geometry is in the file) |
| Family schedule views | manifest `views` | **no** |
| Door swing | SGEO animation keys | yes |

## Export and interchange (from the exporters' own reports)

| Format | Report status | What the report says is lost or approximated |
|---|---|---|
| SGEO | complete, or partial when the project holds scripts, their log or procedural recipes (they are named) | nothing in the model |
| GLB | partial, approximate | parametric families and instance parameters (become plain objects), semantic metadata; geometry tessellated, float32 |
| STL | partial, approximate | identity, names, hierarchy (one merged mesh), units, metadata |
| OBJ | partial, approximate | hierarchy, units, metadata |
| SDRW | complete | constraints round-trip: re-reading an exported drawing gave the same constraints and analysis |
| SVG | partial | persistent constraint relations, metadata, revision |
| DXF | not checked | needs `ezdxf`, which is not installed here |

## Compatibility and migration

- An SGEO file that uses only the earlier parameter types and instance scope has the same `requires` as before. Using a newer type or `type` scope adds the
  capability `parameter-types-v2` to the manifest.
- A reader refuses a file that requires a capability it does not know (`UnsupportedFeatureError`; checked with a made-up capability name). It does **not**
  cross-check that a file using the new types declares `parameter-types-v2`: a file with the marker stripped still loads. Declaring is the writer's job.
- Older SGEO files (0.1.0, 0.2.0) are upgraded in memory on load (the file on disk is not rewritten) and the import report records the source version. Fixtures are assembled the way those writers did, not taken from real old installations.

## Known limits

- Stairs: straight, L or U (two flights and one landing; no wall-clearance check). Headroom is a vertical-ray check; elements that are not boxes or meshes are skipped and counted.
- Facade generator: grid, running bond and a rhythm of listed widths. A user-deleted panel returns when the group regenerates.
- Constraints: 2D in drawings and plan-level between walls, beams and columns; the 3D engine exists and is tested but is not connected to scenes, and solids in space, elevations and sections are not constrained. The analysis is local (first order) and returns one solution.
- Door swing: single-leaf doors only (no panel layouts, no windows). Clearance uses axis-aligned bounding boxes of other elements, which is conservative
  for diagonal walls. Resizing through `model.resize_door` keeps the hinge fixed and re-aims the animation; other edits made directly do not, and `/swing/verify` reports it.
- Running a script holds the application lock for its duration (limited to 10 s by default).
- The words-to-family interpreter is a deterministic pattern matcher for window, door and beam requirements; anything else is reported `unsupported`.
- Not implemented: parametric dimensions driven by parameters, 3D constraints in scenes, learned or predictive features. The client UI does not yet cover the second-pass features.

## Measured performance

`tests/benchmark_parametric.py` records timings (min/median/max, ms) with the machine description; the last run is stored in `docs/parametric_benchmarks.json`
(Linux VM, 2 cores; the Python version is recorded in the file; not a reference machine, and not the Windows desktop). Nothing there is a pass/fail threshold. What the run shows:

- Parameter evaluation is cheap (128 parameters: ~3.7 ms full, ~0.9 ms incremental when one of four chains changes).
- Constraint analysis grows steeply (100 constrained lines: ~0.57 s).
- Editing a family type is now incremental and batched: 100 dependent instances ~62 ms (a full rebuild of the same is ~1.8 s), 300 instances ~0.2 s (full rebuild ~16.6 s). With a single instance the incremental path is slightly slower than a rebuild (1.4 vs 0.9 ms).
- **Placing many instances is still the slow path** (the scene is validated on every apply): 100 instances ~1.7 s, 300 ~16.5 s; 1000 was not measured. Moving a wall that hosts 169 generated panels takes ~6.7 s (not optimised).
