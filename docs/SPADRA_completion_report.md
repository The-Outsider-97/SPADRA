# SPADRA — technical completion report (2026-11-03, ninth revision)

**Ninth revision (2026-11-03, afternoon).** The request of 14:59 (animation hinges, roof types with editable slope, gutter types with rainwater drainage, steel profiles for beams, and more things on a sheet) is described in **Part 00** below, with what was verified and where each part stops. Parts 0 onward are the eighth revision and are unchanged except where Part 00 says so; the test counts in Part 7 are superseded by Part 00.

---

## Part 00 — The request of 2026-11-03 at 14:59

| # | Request | State |
| --- | --- | --- |
| 1 | Doors and windows swing from an end in the animation, matching the 2D drawing | **Done** |
| 2 | Four common roof types, editable later, editable slope | **Done** (flat, shed, gable, hip) |
| 3 | Four common gutter types, editable later, rainwater drainage | **Done**, with the limits below; drainage is a plain area × rainfall flow, **not** a code sizing |
| 4 | HEA / INP / IPE / UPE / L / C / CHS / SHS / RHS profiles for Beam | **Done**; the sizes are nominal table values **that I did not check against a catalogue** (below) |
| 5 | Floor plans, sections, elevations, drawings, schedules, 3D snapshots, models and reports on a sheet | **Done**; "drawings" and "models" are read as described below |

### 1. Hinges
A door's origin is its hinge axis (on the wall face on the swing side) and a window's is the wall's centre line at its hinged jamb; the leaf's geometry is centred on that axis by an offset. The animation turns an object about its origin, so the leaf now turns about the hinge edge, as the 2D swing arc shows. Tests: `tests/test_forms.py` (hinge pivots and the array expectation), `tests/test_animation_authoring.py` (a door swings about its hinge). **Limit:** a window's pivot is its hinged jamb on the wall's centre line; sliding and tilt-and-turn behaviour is not modelled.

### 2. Roofs
`flat`, `shed` (mono-pitch), `gable` and `hip`. Type, pitch (1–80 degrees) and slope (percent rise over run, the same pitch stated the other way, 1.75 %–567 %) are editable in Properties and in the roof picker; the geometry is regenerated from the outline, the type and the pitch. Hip roofs are built by `_hip_mesh` (a slab of constant vertical thickness over a pyramid or ridge; a square outline closes to a point). Catchment areas used by drainage are exact for each type. **Limits:** shed, gable and hip roofs need a four-corner, right-angled outline (a flat roof can have any outline; changing an irregular flat roof to a pitched type is refused, with the reason); no dormers, no overhang or eaves detail beyond the outline; changing a roof's type keeps its name. A change that would remove an eave a gutter hangs on is refused as a whole. Tests: `tests/test_elements.py`.

### 3. Gutters, downpipes and drainage (`gutters.py`)
Four gutter types (half-round, ogee / K-style, box and fascia; chosen from the common types in your chart, not copied from it) with editable size, fall and outlet; each gutter hangs on **one straight eave** of a roof (`roof` + `edge`) and follows the roof when it is edited or moved. A downpipe stands under a gutter at a position along it, with a diameter and a discharge height. Deleting a roof deletes its gutters and their downpipes (Undo returns all). A Gutters / Downpipes category, schedules (with GT / DP marks), NL-SfB suggestions and a Drainage dialog were added.
**Drainage:** the flow a roof sends to a gutter is its plan catchment area (exact for the roof type; a flat roof's area is shared equally among its gutters) × a design rainfall intensity (default 0.03 l/s·m², editable per gutter), shared equally between the gutter's downpipes. **That is arithmetic, not a sizing.** No NEN 3215 or EN 12056 check, no pass or fail, no gutter or pipe capacity.
**Limits:** one gutter per straight eave (no corner pieces, stop ends or brackets, no partial-length gutters); fall toward one end; a downpipe is a straight tube (no bends or shoes); gutters are not draggable (move the roof); flat roofs have no fall. Tests: `tests/test_gutters.py` (25).

### 4. Steel profiles for Beam (`profiles.py`)
Families HEA, HEB, HEM, INP, IPE, UPE, UNP, L (equal and unequal legs), lipped C (cold-formed), CHS, SHS and RHS, plus the rectangle the beam already had (Z, which is in your chart, was not asked for and is not offered). A beam's Profile and Size are editable in Properties and in the Beam HUD, with a Roll angle (quarter turns). Section area, second moment and overall size are read from the outline. The structural analysis uses the profile's own section properties (`section_source`), the schedule has a Profile column, and rectangle / CHS / SHS / RHS use the engine's section where it has one.
**Limits (please read):** the dimensions are **nominal values entered from tables and not verified against any manufacturer's catalogue**; root radii and flange tapers are omitted, so areas differ slightly from catalogue values; the cold-formed C sizes are generic; rolled sections require Steel as the material; the **optimiser refuses profile beams** (it only varies a rectangle). Tests: `tests/test_profiles.py` (15).

### 5. What can be put on a sheet (`sheetviews.py`)
Place View now lists: floor plans, sections and elevations (as before; "drawings" are those — each is an SDRW drawing, and the Project Browser's Drawings list opens the same ones), schedules (as before), and three new kinds:
- **3D model** (live): the model drawn as shaded vector faces from an azimuth and elevation (taken from the 3D view's camera when placed, editable in Properties), at a drawing scale, with outline and crease lines. It follows the model and is part of "changed since issue". **Limits:** it is a parallel (axonometric) projection, not the 3D view's perspective; hidden surfaces are removed by painting faces far to near, which is right for ordinary buildings and can be wrong where surfaces pass through each other; smooth curved surfaces get no silhouette line; voids are not drawn; at most 40,000 faces (more is refused, with advice).
- **3D snapshot** (a picture): the **Snapshot** button in the 3D view's Model ribbon puts the image the 3D view shows now (perspective or orthographic, as you have it) on an existing or a new sheet. The PNG is stored in the project (`snapshots/`), is saved, backed up and recovered with it, and does **not** change when the model does; the camera and the model revision it was taken at are recorded. It is made in the browser from the 3D view, so it needs WebGL 2 and the 3D view on screen (the sheet's own Place View menu says where to find it).
- **Report**: the text and tables of a stored report in a box of a chosen width, text height and number of lines; what does not fit is replaced by a line saying how many lines are not in the box, and a table cell wider than its column is cut with an ellipsis. A report placed on a sheet cannot be deleted until it is removed from the sheet.
Placed things are sized and moved in Properties and take part in the revision comparison (a report and a snapshot are fixed until replaced). New viewports without a position step by 15 mm so they do not land on one another. **Not done:** dragging viewports on the sheet with the pointer, cropping, a legend, dimensions on a sheet. Tests: `tests/test_sheet_contents.py` (11), `tests_ts/math3d.test.ts` (the camera direction), browser `tests_e2e/documentation.e2e.test.mjs` (a new case that takes a real snapshot from the 3D view).

### Tests run for this revision (cloud Linux tree)
| Suite | Result |
| --- | --- |
| SPADRA Python (`python -m unittest discover -s applications/SPADRA/tests -t .`) | 751 run, 751 passed, 2 skipped (the DXF and IFC round trips need `ezdxf` / IfcOpenShell) |
| Shared tools (`applications/tools/tests`) | 62 run, 62 passed |
| TypeScript unit (`npx tsx --test tests_ts/*.test.ts`) | 112 run, 112 passed |
| Type check (`npx tsc -p tsconfig.json`) | clean |
| Build (`npm run build`) | built |
| Browser end-to-end (real server, real Chromium, 20 files) | 178 run, 178 passed. Every file was run once on the final client; two files (`elements`, new `roofing`) were fixed after that first pass and re-run alone: `elements` needed its roof-error text match narrowed (the new RHS profile option also contains the word "rectangular") and `roofing` is new. **Once, in an earlier run, `documentation` failed a viewport count (3 instead of 4) that passed in the three runs after it; its steps use fixed waits, so I believe it is a timing flake, but I did not find the cause.** |

Existing tests changed by this revision: the array expectation and the hinge origin in `test_forms.py`, the Gutters / Downpipes categories in the browser-tree test of `test_elements.py`, and the roof-error match in `elements.e2e.test.mjs`. New: `test_gutters.py`, `test_profiles.py`, `test_sheet_contents.py`, the hinge, roof and properties cases, a TypeScript test of the camera direction, and `roofing.e2e.test.mjs`.

**Not run:** anything on Windows; a visual comparison of the 3D model view on a sheet with the 3D view beyond one screenshot I looked at; printing a sheet with a snapshot to paper or PDF; the profile dimensions against any catalogue.

---

## Part 0 — The request of 2026-11-03

| # | Request | State |
| --- | --- | --- |
| 1 | Drag, rotate, move, stretch walls (as in Revit) | **Done** for walls and the other model elements; not for 2D drawing entities (below) |
| 2 | Build walls in 2D views | **Done** (Wall tool in plan views, chained, snapping) |
| 3 | Rooms made automatically when walls close a space | **Done**, with the limits below |
| 4 | Built-in NL-SfB coding | **Done** (tables 0–4 shipped, picker, schedules, tags) |
| 5 | More freedom in 3D, the selection as centre of rotation | **Done** |
| 6 | Section lines moveable, stretchable, resizable | **Done** (on a plan) |
| 7 | Grid snap optional in 2D and 3D | **Done** |
| 8 | Extrude, Make void, Sweep, Array | **Done**, with the limits below |

### 1 and 2. Wall editing and 2D wall drawing
- `model.set_wall_ends` stretches one or both ends in one command; `model.rotate` turns any number of elements about a point (all or none); `model.move` already existed. Each goes through `regenerate_wall`, so the joints of the neighbouring walls and the hosted doors and windows are re-derived once the edit is final, and anything that no longer fits (a door left beyond the end) refuses the whole edit. One Undo reverses one edit.
- In the plan: square grips at the ends of a selected wall (drag, with snapping to the ends, middles and centre lines of other walls), the wall's length shown and typeable, a round rotate handle and the Rotate tool (Q), a ghost while dragging, and the Wall tool drawing chains of walls with a height and thickness strip shared with the 3D Wall tool.
- Rotation works on walls, grids, beams, columns, floors, roofs, rooms, stairs, generic models, extrusions, voids and sweeps. A door or window turns with its wall and is refused on its own. **Not done:** rotating 2D drawing entities with this tool; flip arrows; a stretch handle for floors or rooms (their corners are edited in Properties by numbers or by redrawing).
- Dragging with grid snap on moves by the snapped difference of the two pointer positions, not to a grid point.
- Tests: `tests/test_wall_editing.py` (14), browser `tests_e2e/wallediting.e2e.test.mjs` (10), TypeScript `geom2d` tests.

### 3. Automatic rooms (`autorooms.py`)
- Walls on a level are cut at crossings into a planar network of centre lines; spaces are found by a face walk (inside on the left), dangling ends and bridges are pruned, and each outline is inset by half of each bounding wall's thickness. Rooms are matched across edits by a stored probe point, so a room keeps its identity, name and number when its walls move. A room nobody has changed disappears when its walls open the space; one the person renamed, renumbered or resized is kept and flagged (`open`; the model check warns it is no longer enclosed). A hand-drawn room is never touched; drawing one over an automatic room replaces it. Auto rooms is a project setting (on by default; Insert has the switch) and `model.update_rooms` recomputes.
- Auto-created rooms roll back with Undo, in the same command as the wall edit that made them.
- **Limits:** only walls enclose (not columns, beams or floors); a space containing a separate ring of walls (an island) is skipped rather than given a hole; walls must meet within 2 mm; arcs are not applicable (walls are straight). Editing several walls one after another can open a loop for a moment: a room you have named or numbered survives that (kept and flagged), an untouched automatic room is removed and made again when the loop closes, and may then get a different number.
- Tests: `tests/test_autorooms.py` (29), browser `tests_e2e/autorooms.e2e.test.mjs` (5).

### 4. NL-SfB (`sfb.py`, `data/nl_sfb.json`, `data/build_nl_sfb.py`)
- The tables are the supplied spreadsheet (NL/SfB 2005 tables 0–4, edition 2019-12-23; 2231 rows: 554 / 1010 / 26 / 152 / 489), read by `build_nl_sfb.py` with nothing renamed, merged or added; one row's text, saved through the DOS code page, was restored (`repaired_texts`). A few codes occur twice with different texts in the source; both are kept. Searching is by number or by a Dutch or English word, accent-insensitive.
- Every element can carry the element code (table 1; rooms table 0), a construction method (table 2) and a material (table 3), stored in the element's own metadata. A code not in the table is refused. Nothing is classified automatically; the picker suggests the usual codes for a category and the person chooses. The code is a column of every schedule and a field of tags.
- **Not done:** table 4 (activities, requirements) is shipped and searchable but no element field uses it; no automatic mapping from SPADRA's materials to table 3; the PDF you supplied (`NL-SfB-Codering.pdf`) was not used as a data source.
- Tests: `tests/test_sfb.py`, schedule tests, browser `tests_e2e/sfb.e2e.test.mjs` (5; types a Dutch word, sets, refuses and clears a code). The bundle test unpacks the shipped data file.

### 5. 3D navigation
- Selecting an object makes it the centre of rotation (a marker shows the pivot; the "orbit" toggle on the rail turns it off, and with nothing selected the view orbits the model centre as before). The wheel zooms towards the pointer, Ctrl-drag looks around, arrows orbit, Ctrl-arrows walk, PageUp / PageDown move vertically. Pure maths in `math3d.ts` (tests: `tests_ts/math3d.test.ts`); browser `tests_e2e/navigation3d.e2e.test.mjs` (7).
- **Not measured:** feel on a trackpad or touch device.

### 6. Section lines
- A section mark on a plan is selectable; its two end grips stretch it and the line can be dragged to move it (`view.set_section`); the section drawing is rebuilt from the model at the new position. `tests_e2e/wallediting.e2e.test.mjs` has a test that stretches and moves a mark with its grips. Changing the section's depth or direction is done in Properties, as before.

### 7. Grid snap
- A switch (View menu, the status bar, F9) governs the plan and the 3D view; its state is kept in this browser (`localStorage`), not in the project.

### 8. Extrude, Make void, Sweep, Array (`forms.py`, `arrays.py`)
- **Extrude** (`model.add_extrusion`): an outline in plan stood up from a base to a depth, in a material; an exact prism (SGEO B-rep with holes when a void cuts it). Properties edits depth, base, material and level; it moves and turns by its parameters.
- **Make void** (`model.make_void`, and the Solid / Void switch in Properties): the same shape as a cutter. A void is drawn see-through in 3D, hatched in plan, left out of quantities, sections, elevations and GLB / STL / OBJ exports (a native SGEO export keeps it, as it is part of the model's data).
  What it cuts is only what is computed exactly: (a) a **floor** or solid **extrusion** gets a hole when the void lies wholly inside its outline and covers its full thickness (verified by volume to 1e-9 m³); (b) a **wall** gets a rectangular opening when the void is a rectangle square to the wall passing through its whole thickness (at the ground, to the top, or splitting the wall; verified by volume). Everything else cuts nothing and says why in Properties and in the model check: a void across the edge of a floor, one that stops inside the slab, a niche, a void at an angle to a wall, or anything against a roof, beam or column. Two voids that overlap inside one floor or wall are refused. Cuts follow later edits: moving, resizing, converting or deleting a void, or moving the floor or wall, rebuilds the solid inside the same command (`forms.reconcile`), and Undo restores both together. A void that would make a wall opening overlap a door is refused.
- **Sweep** (`model.add_sweep`): a profile (rectangle, ellipse, or any polygon section given as `section`) carried along a plan path at one height, mitred at corners, open (capped) or closed (a ring). Volumes are verified (a straight run, a mitred L and a closed ring equal profile area × centre-line length). **Limits:** the path is flat (no climbing, no 3D path, no arcs; approximate a curve with segments); a corner sharper than about 151° of turn (mitre ratio 4), a profile wider than its corners allow, a path that crosses itself or doubles back is refused; the profile keeps its orientation (it does not roll).
- **Array** (`model.array`, a dialog reached from Insert, Model and Modify, usable from the plan and the 3D view): linear (step x, y, z in drawing units) or radial (centre and angle; 360° divides the circle evenly, any other angle puts the last item on it), 2 to 500 items, at most 2000 copies in one command, all or none. Copies are made with SGEO's `duplicate` and then moved or turned by the same code as Move and Rotate, so joints, hosted openings and voids are worked out for each copy. A wall brings its doors and windows. **Limits:** copies are independent elements (no live group that updates when the original changes, no changing the count afterwards); doors, windows and rooms alone are refused; no preview ghost of the copies and no picking of the step on the plan (numbers are typed).
- Placement tools (Extrude, Void, Sweep) are drawn on plan views; in the 3D view Make void and Array work on the selection, but those three shapes are not placed there.
- Tests: `tests/test_forms.py` (28), browser `tests_e2e/forms.e2e.test.mjs` (6: draws an extrusion with the pointer, a void that opens the floor and closes again on delete, Make void, a sweep, the Array dialog).

### Verification run for this revision
| Check | Result |
| --- | --- |
| `python -m unittest discover -s applications/SPADRA/tests -t .` | 692 tests OK (2 skipped: the DXF and IFC round trips, libraries not installed) |
| `python -m unittest discover -s applications/tools/tests -t .` | 62 tests OK |
| `npx tsc -p tsconfig.json` | no errors |
| `npx tsx --test tests_ts/*.test.ts` | 110 tests OK |
| `npm run build`, then every file in `tests_e2e/` (19 files, real Chromium, real pointer input) | all pass; see the note below |

Two browser tests failed during this run and were not caused by the new features: the door-schedule test still expected the column list from before the NL-SfB column was added (test updated), and the animation test "applies a quick motion in one step" failed deterministically because the Quick motion panel folded shut when its list was changed before the panel's own toggle event had been recorded (`timeline.ts`; a race that already existed, fixed by recording the open state when the list changes). Both files then passed in full.
**Not verified:** anything on Windows; the Windows build of the client; touch and trackpad behaviour; the Array dialog and the option strips with a screen reader.

---

---

## Part 1 — Implemented functionality (this revision)

### 1. Walls that meet at an angle

Reported problem: walls drawn at an angle overlapped each other at the corner (a "bow tie") instead of joining. Now:

- Each wall end that reaches another wall is **mitered** to the shared corner for an L, or **butted** for a T, using the end of the other wall's face lines (`miter_node`, `butt_ends` in `geometry.py`; the extension of each end is stored in the wall as `spadra:ends`, the partner as `spadra:joins`).
- Joins are made when a wall is drawn and **refreshed for the neighbours** whenever a wall is moved, edited (end points, thickness, level), deleted, undone or redone; they are always derived from the walls (a test checks the stored joints equal the derived ones) and survive save, close, open and a full rebuild. A wall end with no partner stays square; walls that only come close are not joined; a corner too sharp for a mitre stays square rather than spiking; the order the walls are drawn in does not matter. A door or window cannot be left inside a corner (the edit is refused) and is kept clear of a corner when placed.
- Verified in Python (`tests/test_wall_joins.py`, 20 tests): right-angled, angled and different-thickness corners with matching faces and neither bow tie nor overlap; three walls at one point; a closed rectangle of exact area; a T junction cut flush to the face, including a slanted branch; planar, closed solids. Verified in the real interface (`tests_e2e/walls.e2e.test.mjs`, 4 tests): two walls drawn with the Wall tool at an angle share exactly the two mitre points in the model and on the plan, the outlines do not overlap (measured on a 400 × 400 grid), the model check finds nothing, Move re-joins the corner and Undo restores it exactly.
- **Not done / not tested:** four or more walls at one point were not tested; there is no per-corner control to turn a joint off or choose butt instead of mitre; no curved walls.

### 2. Connection to SLAI's commit adapters (item 1 of the request)

The adapters (`src/agents/planning/project_adapter.py`, `commit_reconciliation.py`, `revision_workflow.py`, `workflow_runtime.py`) deliberately supply no implementation: the application must provide atomic expected-revision checking and a stable idempotency lookup. The shared part is written once, in `applications/tools/commit_host.py` (`CommitHost`): one critical section around "is the artifact still at the expected revision" and "apply", the change and its ledger entry in one transaction, a decided idempotency key returns its stored receipt byte for byte, a key reused for a different request is refused, and a failure nobody can classify propagates so the outbox records it as **unknown** and resolves it by lookup, never by re-sending. No revision is invented.

- **SPADRA** (`SPADRA/commits.py`). One artifact, `project`, whose revision is the project revision. The "proposal" is the assistant's proposal; the "consent" is the person's Approve (`Assistant.consent`); the commit runs through `Project.execute` like any edit (all steps or none, undoable). The interface's Approve now goes through `CommitReconciliationStore`. Receipts are stored in the project manifest (`commits`), so they are saved with the project and not undone by Undo.
- **SPlanner** (`splanner/commits.py`, schema 3→4 migration in `store.py`). Artifacts `event:<id>` and `task:<id>` map to the record's existing `version` (already checked in the same SQL statement as the update). Proposals are stored and validated by running the real update inside a rolled-back transaction (`POST /api/proposals`); a person approves (`/approve`) or rejects (`/reject`); the decision is written to `commit_ledger` **in the same SQLite transaction** as the change. **Backend only: SPlanner's interface has no screen for proposals** (`capabilities.agentSuggestions` stays false), and no SLAI agent creates SPlanner proposals today.
- `--check` reports whether the adapters import; without them the application still starts and approving an assistant proposal is refused with that reason.
- The planning modules are the user's. They were copied into the **cloud test tree only**, from the files already staged on the device; nothing in `src/agents/planning/` was edited and nothing was written to it on the device.

### 3. Phase 26 — startup, build, packaging

- `python -m applications.SPADRA` starts the server on loopback (default 8770) with the **active** interpreter; it never looks for, creates or switches a virtual environment. Before starting it verifies Python (against `requires-python` in SLAI's `pyproject.toml` when it declares a range, else 3.10 or newer), required packages (PyYAML) and the built web client; each failure stops with the reason and the command that fixes it (`startup.py`, `StartupError`).
- `--check` (report, exit 0 only when SPADRA can run), `--build` (the shared static build, `npm run build`; `npm.cmd` on Windows), `--dev` (rebuild the client first when missing or older than its sources), `--no-browser`.
- `requirements.txt` lists exactly what was imported and the versions the tests ran with. `package.json` scripts: `start`, `start:dev`, `check`, `build`, `package`, `typecheck`, `test`, `test:py`, `test:e2e`, `test:all`.
- `python -m applications.SPADRA.bundle` writes a **source bundle** (zip): SPADRA, the shared tools, the SLAI libraries it imports, and the built client. A test unpacks the bundle into an empty folder and starts it from there. **It is not an executable and not an installer**; the target machine needs Python and the packages.

### 4. Phase 27 — code quality

- Removed SPADRA's own hatch clipping (`geometry.hatch_lines`), which duplicated the SDRW library's `sdrw.annotations.hatch_lines`; `project.hatch_strokes` now calls the library. Hatching therefore covers every outline kind the library supports (the library places its strokes at different offsets than SPADRA's old code did, so the hatch tests were rewritten to check coverage, spacing and clipping rather than fixed positions).
- `draw.hatch` now validates by computing the strokes, so a spacing that could never be drawn is refused and nothing changes.
- Broad `except Exception` narrowed where it could hide a defect: "does the element/entity exist" now catches only the library's `SpatialFormatError`; quantities records a note only for the library's or SPADRA's own refusals (a defect propagates; a test proves it). Remaining broad handlers (checked one by one): agent loading (records why the agent is unavailable), the agent bridges (translate the failure into a structured error), families (wrap the library's message in a `BadRequest` shown to the person), import/export adapters (`_refused` classifies and reports the exception type and message, because third-party parsers can raise anything), jobs (a job must always end in a state the interface can show), diagnostics (a log must not turn one failure into two) and the command route (records, then re-raises). None of them continues silently.
- `elements.alive` is the one place that asks whether an id still exists.
- TypeScript: `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride` (shared `tools/tsconfig.json`); no `any` in the client or its tests; `tsc` clean.
- **Naming:** the outdated name "SPADE" appears nowhere in the source tree, documentation or the built client (searched case-insensitively). The package docstring now uses the same expansion as the README ("Spatial Parametric Authoring, Design & Engineering"); before, it said "…Representation & Analysis".
- The quantities take-off measures each element alone in a scratch SGEO document with the same geometry and world transform, so its cost no longer grows with the number of other elements (SGEO's own `volume()` re-validates the whole scene on every call). Measured in Part 8.

### 5. Documentation

README (run/check/build/package table, walls, commit connection, what is not verified), `tools/README.md` (the commit host), SPlanner README (proposals API, backend only), and this report. All were edited to say only what exists.

---

## Part 2 — Files and modules modified or added in this revision

Under `applications/SPADRA/` unless stated.

| Change | Files |
| --- | --- |
| New | `startup.py`, `bundle.py`, `requirements.txt`, `commits.py`, `tests/test_startup.py`, `tests/test_bundle.py`, `tests/test_commits.py`, `tests_e2e/walls.e2e.test.mjs` |
| New (shared) | `applications/tools/commit_host.py`, `applications/tools/tests/test_commit_host.py` |
| New (SPlanner) | `applications/splanner/commits.py`, `applications/splanner/tests/test_commits.py` |
| Modified | `app.py` (run modes, `--check/--build/--dev`, commit host), `package.json` (scripts), `project.py` (commit ledger carried through undo/redo/save-as/recover, hatch via library, hatch validation), `assistant.py` (consent / run / approve split, `outcome`), `routes.py` (approve through the commit host), `geometry.py` (wall joins; own hatch clipping removed), `elements.py`, `annotations.py`, `interop.py`, `quantities.py`, `relations.py`, `documentation.py` (error handling and reuse, Part 1 §4), `utils/spadra_errors.py` (`StartupError`), `__init__.py`, `README.md`, `docs/SPADRA_completion_report.md` |
| Modified tests | `tests/test_quantities.py`, `tests/test_modify2d.py`, `tests/test_security.py` (a socket was left open when the server hung up early), `tests/test_wall_joins.py` |
| Modified (shared) | `applications/tools/README.md` |
| Modified (SPlanner) | `store.py` (schema 3 → 4: `commit_ledger`), `app.py`, `routes.py` (`/api/proposals`), `README.md`, `tests/test_api.py`, `tests/test_repeats.py`, `tests/test_store.py` (schema version assertions) |
| Not touched | `src/` (SLAI libraries and agents); the user's four `src/agents/planning/*.py` modules (copied into the cloud test tree only, unmodified) |

## Part 3 — Existing SLAI components reused

- `src/agents/planning/project_adapter.py` (`HostCommitRequest`, `HostCommitReceipt`, `AuthoritativeProjectAdapter`), `commit_reconciliation.py` (`CommitReconciliationStore`, `AuthorizationContext` scopes), `revision_workflow.py` (`RevisionWorkflowStore`): used, not copied or re-implemented.
- `src/utils/spatial_formats` (SGEO, SDRW, SLDC, container, OBJ/GLB/STL/SVG adapters), including `sdrw.annotations.hatch_lines` (now used for hatching); `src/utils/sldc` for reports.
- The five agents SPADRA integrates (Part 5) from `src/agents`.
- `applications/tools`: loopback server, token and cookie handshake, response envelope, `AppError` family, validation helpers, atomic JSON storage, the shared static build (`tools/build/build-static.mjs`) and TypeScript configuration, UI kit.
- Python standard library only for the bundle (`zipfile`) and the outbox (`sqlite3`).

## Part 4 — Shared components extended

- `applications/tools/commit_host.py` (new): `CommitHost`, `Applied`, `CommitRefused`, `Outbox`, `FileCommitLedger`, `planning_types()`. Used by SPADRA and SPlanner. Its 15 tests cover the critical section, idempotent replay, key reuse for a different request, a crash between change and ledger, refusal, unknown outcomes, and the file ledger.
- `applications/tools/errors.py` and the rest of the shared kit were **not** changed; `commit_host` uses `DependencyMissing`, `Conflict`, `BadRequest`, `NotFound` as they are. The shared tools suite (62 tests) passes.
- SPlanner's database schema moved from version 3 to 4 (additive: a new `commit_ledger` table); the migration is tested on an older database that holds data (`tests/test_store.py`: the data is kept and the store writes a backup of the old file, `migration_backup`, before migrating). **An existing SPlanner data folder is migrated on first open.**
- Nothing in `src/` was changed.

## Part 5 — Agents integrated

SPADRA integrates **5 of the 25** SLAI agents, each imported and called for real (`--check` and Analyze → Agents show whether each imports on the machine): **STEM** (units, formulas, constraints, stiffness, deflection evaluation), **Simulation** (single-degree-of-freedom beam response), **Spatial** (distances, bounding-box relations), **Optimization** (beam depth/width search), **Animation** (evaluating the keyframed spec; export of frames). The other 20 (Language, Planning, Reasoning, Knowledge, Alignment, Adaptive, Learning, Safety, Evaluation, Perception, Network, Verification, Reader, Execution, Provenance, Quality, Privacy, Handler, Collaborative, Observability) are **not integrated**; the dialog says so and gives the reason.

The commit path uses SLAI's planning **adapters** (not the Planning Agent). **The assistant is a pattern interpreter with twelve request forms, not a language model**; its proposals are validated on a scratch copy and run only after a person approves.

## Part 6 — Workflows verified

Each was run (not described) by the test named. "Browser" means real Chromium against the real server.

| Workflow | Evidence |
| --- | --- |
| Draw two walls at an angle; they join; Move re-joins; Undo restores | `walls.e2e` (browser), `test_wall_joins` |
| Ask the assistant for a wall → proposal waits → Approve → wall exists, verified by read-back → Undo | `assistant.e2e` (browser), `test_assistant`, `test_commits` |
| Approve the same proposal twice or after the project moved on | `test_commits` (second approval returns the stored receipt and runs nothing; a stale revision is refused and nothing changes) |
| Approval whose outcome is uncertain | `test_commit_host`, `test_commits` (recorded as unknown; resolved by lookup, never re-sent) |
| SPlanner: propose → approve changes one event; reject changes nothing; stale version refused; same key replays | `splanner/tests/test_commits.py` (13 tests) |
| Start with `python -m applications.SPADRA`, load the page, call the API | run by hand for this report (output in Part 7), `test_startup::Launch`, all browser suites |
| Check, build, bundle; start from the unpacked bundle | `test_startup`, `test_bundle` |
| Offline: the key suites with every non-loopback network use made to fail | 152 tests, zero attempts (Part 7) |

## Part 7 — Tests executed and results

All run in this session on the final tree (cloud Linux, Python 3.13.18, Node with Playwright/Chromium, the real SLAI agents present).

| Suite | Command | Result |
| --- | --- | --- |
| SPADRA Python | `python -m unittest discover -s applications/SPADRA/tests -t .` | 604 run, 604 passed, 0 failed, 2 skipped (the DXF and IFC round trips need `ezdxf` / IfcOpenShell) |
| Shared tools | `python -m unittest discover -s applications/tools/tests -t .` | 62 run, 62 passed |
| TypeScript unit | `npx tsx --test tests_ts/*.test.ts` | 95 run, 95 passed |
| Type check | `npx tsc -p tsconfig.json` | clean (exit 0), `strict` + `noUncheckedIndexedAccess` |
| Browser end-to-end | `SPADRA_E2E_ROOT=<tree with src/agents> node --test tests_e2e/*.e2e.test.mjs` (14 files, real server, real Chromium) | 140 run, 140 passed, 0 failed, 0 skipped across 20 suites (~8 min) |
| Offline | the API, project, assistant, agent, animation, calculator, commit and security suites with every non-loopback `connect` / `getaddrinfo` made to fail | 152 run, 152 passed, **0** attempts to reach the network |
| SPlanner | `python -m unittest discover -s applications/splanner/tests -t .` (dev copy) | 119 run, 91 passed, **28 failed — all the same pre-existing `sldc.version_key` error (Part 9)**; the 13 commit tests pass |
| Startup (by hand) | `python -m applications.SPADRA --no-browser --port 8791 --data-dir <empty>` then fetch the printed URL, follow the redirect, call `/api/bootstrap` | server printed its URL; `/` answered 302; with the token the page answered 200; `/api/bootstrap` returned `{"ok": true, "data": {"app": {"name": "SPADRA", "version": "1.0.0-dev", ...}}}`; stopped cleanly |
| Check (by hand) | `python -m applications.SPADRA --check` | in the tree with agents: 0 blocking, 0 warnings (Python 3.13.18, PyYAML 6.0.3, numpy 2.5.3, scipy 1.18.1, psutil 7.2.2, client built, 5 agents import, commit adapters import). In the tree without `src/agents`: the commit-adapter warning ("approving assistant proposals will be refused until it is") |
| Check on your machine's Linux VM | `python3 -m applications.SPADRA --check` in the delivered tree (Python 3.10; not Windows) | Ready, 0 blocking, **8 warnings**: not in SLAI's virtual environment (`venv`, which it names); scipy not installed; the web client reported older than its sources; commit adapters import (ok); **all five agents fail to import** with `AttributeError: module 'colorama' has no attribute 'just_fix_windows_console'` (the VM's colorama is older than the one SLAI's agents need; I believe that function arrived in colorama 0.4.6). SPADRA starts without agents and says why. Not fixed: environment changes on your machine are yours to make |

**Found by running the whole thing, and fixed:** running the browser suites against a SLAI root without `src/agents` made the assistant's Approve fail, because approval now needs the commit adapters. That is the intended dependency, but `--check` did not say so; it now reports it (new tests in `test_startup`). A test that left a socket open when the server hung up early (`test_security`) was also fixed.

**Not run:** anything on Windows; DXF, IFC, DWG, STEP, PDF export; a screen reader; a touch device; a visual comparison with your screenshots; the agents that cannot be imported here; SPlanner's browser tests.

### Acceptance criteria (Phase 29)

PASS means the behaviour was run and checked by the evidence named; PARTIAL names what was not.

| # | Criterion | Verdict | Evidence / what is missing |
| --- | --- | --- | --- |
| 29.1 | `python -m applications.SPADRA` really starts | **PARTIAL** | Started and answered on Linux (Part 7); unpacked bundle starts (`test_bundle`). **Not run on Windows.** |
| 29.2 | UI matches the screenshots | **PARTIAL** | Built from them; deviations are listed in Appendix B; no pixel comparison, no screen-reader run |
| 29.3 | 2D editing | PASS | `test_modify2d` (44), `modify.test.ts`, `geom2d.test.ts`, `modify2d.e2e` |
| 29.4 | 3D editing | PASS | `test_elements` (35), `test_project`, `elements.e2e`, `walls.e2e` |
| 29.5 | Parametric regeneration | PASS | parameter update regenerates the element in one undo step (`test_project`, `test_bim`; 263 ms median at 267 elements) |
| 29.6 | BIM semantics | PASS (as scoped in Part 9) | `test_bim` (55), `test_rooms` (24), `workspace.e2e` |
| 29.7 | 2D/3D coordination | PASS | views are derived from the model on every read; tags re-derive inside the same command (`test_documentation` 43, `documentation.e2e`) |
| 29.8 | Section A-A | PASS | `test_sheets` (Section A-A workflow), `test_documentation`, 80 ms to generate |
| 29.9 | Sheet A101 | PASS | `test_sheets` (A101 from creation to a revision, with change detection), `test_reports`, print to PDF in Chromium (`accessibility.e2e`) |
| 29.10 | Engineering calculations | PASS (as scoped) | `test_calculator` (7), `test_structural` (14), `engineering.e2e`; real STEM, Simulation and Optimization agents |
| 29.11 | Assistant uses real SLAI capabilities | PASS | `test_assistant` (21), `test_agents` (10), `assistant.e2e`; no language model is claimed |
| 29.12 | Agent proposal validated and run through SPADRA's command system | PASS | proposal validated on a scratch copy, run only after approval through `Project.execute` and the commit adapters (`test_commits` 14, `assistant.e2e`) |
| 29.13 | Animation | PASS | `test_animation` (19), `test_animation_authoring` (34), `animation.e2e` |
| 29.14 | Native persistence | PASS | SGEO / SDRW / SLDC only, hash-checked, backups, recovery (`test_project` 48, `project.e2e`) |
| 29.15 | Only verified import / export | **PARTIAL** | Verified on real files: SGEO, SDRW, SLDC (export), GLB, STL, OBJ, SVG. **DXF and IFC are enabled when their libraries are installed but were never run; DWG, STEP and server-side PDF are reported unavailable** |
| 29.16 | Offline operation | PASS | no external URL in the built client; 152 tests with all non-loopback networking disabled, 0 attempts |
| 29.17 | Failed / cancelled operations keep integrity | PASS | commands snapshot and roll back (`test_project`), jobs cancel (`test_jobs` 23), a refused assistant step undoes the earlier ones (`test_commits`), a refused hatch changes nothing (`test_modify2d`), hostile input leaves the revision unchanged (`test_security`) |
| 29.18 | Repository consistency | **PARTIAL** | no commit, push, deletion or publication; one implementation of each format and of hatching; no unexplained dependency. **But** two of the applications with revision systems are not connected (Part 9) and SPlanner's suite has 28 pre-existing failures |

## Part 8 — Performance findings

Measured now with `python -m applications.SPADRA.tests.measure_performance --walls 200` (267 model elements, median of 5 runs, milliseconds, cloud machine, Python 3.13.18, Linux):

| Operation | min | median | max |
| --- | --- | --- | --- |
| open project (read files, verify hashes) | 290 | 300 | 328 |
| open a drawing (floor plan) | 67 | 76 | 77 |
| load the 3D scene | 114 | 121 | 122 |
| select an element (read properties) | 34 | 38 | 56 |
| update a parameter (regenerates the wall, one undo step) | 253 | 263 | 305 |
| generate a section drawing | 80 | 80 | 123 |
| quantities of every element | 272 | 278 | 480 |
| save (second save: files and backup) | 340 | 380 | 406 |
| animation playback: one frame | 125 | 135 | 156 |

Same script at the three sizes of the previous table (median ms; the previous figures are in brackets where I have them):

| Operation | 27 elements | 80 elements | 200 elements |
| --- | --- | --- | --- |
| open project | 41 (36) | 109 (104) | 242 (231) |
| open a drawing (floor plan) | 8 (6) | 19 (17) | 60 (52) |
| load the 3D scene | 20 (13) | 38 (51) | 89 (102) |
| select an element | 7 (4) | 13 (9) | 23 (17) |
| update a parameter (one undo step) | 32 (25) | 82 (67) | 193 (161) |
| generate a section drawing | 10 (13) | 30 (28) | 70 (56) |
| save (second save) | 69 (70) | 131 (144) | 301 (287) |
| animation playback, one frame | 20 (18) | 43 (44) | 103 (99) |
| **quantities of every element** | **33 (140)** | **100 (1 036)** | **239 (6 603)** |

- **Quantities are no longer quadratic**: each element is measured alone, so the take-off grows roughly with the number of elements. The previous report's note of an unexplained 1 036 ms vs 111 ms difference between two quantities runs was not reproduced and not investigated.
- The other rows are within run-to-run noise of the previous table except that editing operations are about 20 % slower (update a parameter 82 vs 67 ms at 80 elements, 193 vs 161 ms at 200), which is consistent with the model check that every edit now re-reads (noted in the previous revision); I did not profile it. Several single runs are noisier than their medians (for example quantities, max 237–447 ms).
- Not measured: browser frame rate, memory, behaviour above ~270 elements, your Windows machine.

## Part 9 — Known limitations

**Applications not connected to the commit adapters (item 1 of the request, only partly met).** I searched every application on your machine for a revision or commit system (keyword search of the Python sources, then reading the revision code in Writer and Documaster; the others were **not** examined in depth). The adapters need an authoritative artifact with a revision that can be checked atomically, and a *proposal that a person approves*; where the application had no such proposal flow I did not invent one.

| Application | What it has | Connected? |
| --- | --- | --- |
| SPADRA | project revision, command system with rollback, assistant proposals with recorded consent | **Yes** (tested) |
| SPlanner | per-record `version` checked in SQL, transactions | **Yes, backend only** (tested; no screen for proposals) |
| SLAI Writer (`slai_writer`) | `Session.apply(op, base)` under a lock, refuses a stale `base` (`stale_version`), revision numbers, signatures | **No.** The nearest fit of all: a connection needs a proposal store, a person's approval flow, and a decision about what a signed revision means. Not done, because that is a product design to agree on, and I could not run Writer's whole interface here. |
| Documaster | uploaded documents with a list of revisions (undo/redo cursor), SLDC revision ids | **No.** Revisions are file copies for undo/redo, not a project with an expected-revision commit; there is no agent proposal flow. |
| Reader | annotations stored beside the document; `revision` is the document's SLDC revision id | **No.** I found no flow in which Reader changes the document itself. |
| SciCal, GangUP, CADCon, secure_folder | only incidental uses of the word "revision" or "stale" in the search; no revision-managed project artifact seen | **No** (not examined in depth). |
| bimap, AIDAP | "revision" means audit-rule versions and submitted document revisions (assurance), not an editable project | **No.** |

**Packaging and platform**
- The package is a **source bundle** (zip), not an installer or a signed executable. Nothing was run on Windows; `npm.cmd` handling and Windows paths are covered only by unit tests that do not run Windows.
- `requirements.txt` lists versions the tests ran with (PyYAML 6.0.3, numpy 2.5.3, scipy 1.18.1, psutil 7.2.2, Python 3.13.18); older versions were not tried. `requirements.txt` records that SLAI's `pyproject.toml` on your machine declares `>=3.10,<3.13`; that file is not in the cloud tree, so the tests here ran on 3.13 without that range being checked (`--check` reads the range when the file exists, and a test covers the newer-than-declared warning).

**Functionality** (carried forward; nothing here was changed by this revision)
- A **language-model assistant** and the agents of 14 that need PyTorch / statsmodels.
- **Simulation** beyond one beam, one mode, two scenarios. **Optimisation** beyond one beam dimension against the deflection limit. **Spatial** relations are between bounding boxes. **Calculator**: SI only.
- **BIM:** curved, spiral or landing stairs; building-code checks of any kind; importable / exportable family files, nested families, shapes beyond box / cylinder / sphere; parametric constraints that drive other elements; MEP; structural design checks beyond the deflection limit.
- **Coordination:** exact clash detection and IFC coordination (the conflict test uses bounding boxes only); 4D / 5D; multi-user collaboration; comments.
- **Documentation:** sheet dimension and legend tools, viewport cropping, title-block layout editing, sheet export to DXF / DWG / SVG, server-side PDF (printing uses the browser's print dialog), camera animation and rendering.
- **Interchange:** DXF / IFC / DWG / STEP / PDF were **never run on real files** (`ezdxf`, `ifcopenshell`, `reportlab`, `OCP` are not installed here; the two skipped tests are the DXF and IFC round trips). SPADRA reports those formats as unavailable instead of writing a file. SVG import is a strict subset; OBJ / STL / GLB carry no semantics.
- Hatch editing UI, glass transparency, editable schedules, 3D dimensions, recovery after a crash while editing (backups exist only from a save). Unimplemented tools are shown **disabled** with a tooltip saying where the same thing is done today.
- **Accessibility:** names, contrast of the interface, keyboard and layout were measured by script; **no screen reader was run**, no touch device was tried, and contrast of drawn content was not measured.
- **Visual fidelity:** there was no pixel comparison with your screenshots (deviations are listed at the end).

**Pre-existing, not caused by this work (reported, not fixed):** in the SPlanner copy I tested (your SPlanner files plus the `src` libraries staged from your machine, in the cloud tree), **28 of 119 SPlanner tests fail** with `AttributeError: module 'src.utils.sldc' has no attribute 'version_key'` — SPlanner's `store.py` (line 440) calls `sldc.version_key` / `KNOWN_VERSION`, which the `src/utils/sldc.py` I was given does not define. All 28 failures have this single cause. The commit tests (13) pass. Notes (SLDC) in SPlanner are affected until `src/utils/sldc.py` and SPlanner agree again; I did not change either shared module.

## Part 10 — Remaining release blockers

1. **Windows is untested.** Startup, the build (`npm.cmd`), paths, the browser and the bundle must be run on your machine (`python -m applications.SPADRA --check`, then `--build`, then the tests) before SPADRA can be called releasable.
2. **No installer.** Distribution today is a source bundle plus Python and package installation. A desktop package (installer, signed executable, bundled interpreter) would need a packaging tool decision (for example PyInstaller or an Electron/Tauri shell) and would have to be tested on Windows.
3. **On your machine's Linux VM the agents do not import** (old `colorama`) and scipy is missing, so the engineering, simulation, optimisation and animation features would be unavailable there until `pip install -r applications/SPADRA/requirements.txt` and a newer `colorama` are installed in SLAI's `venv`; the web client also needs `npm run build` if its build is older than its sources.
4. **Commit adapters are connected in two of the applications that have a revision concept, not in Writer or Documaster.** Writer is the natural next one; it needs your decision on proposals and signed revisions (Part 9).
5. **SPlanner has no screen for proposals**, and nothing creates proposals for it yet, so the connection can only be exercised through its API.
6. **Commit approval depends on the planning modules.** `src/agents/planning/{project_adapter,commit_reconciliation,revision_workflow,workflow_runtime}.py` import on your machine (`--check` there says so); in any checkout without them, SPADRA's Approve is refused with that reason. In the cloud test tree they are unmodified copies of your files.
7. **Interchange formats not run:** DXF, IFC, DWG, STEP, PDF export; SPADRA reports them unavailable until the libraries are installed and the round trips are run.
8. **Shared-library edit still pending your decision** (from earlier revisions): saving a model that has an animation needs `"animation-v1"` and `"animation-v2"` in `FEATURES["0.2.0"]` in `src/utils/spatial_formats/container/versions.py`; applied in the cloud copy only. `jsonschema` 4.x or newer is required on your machine.
9. **SPlanner's 28 failing tests** (`sldc.version_key`) block a clean SPlanner release independently of this work.
10. **Open quality items:** 4+ walls at one point untested; no screen-reader or touch test; hatch-pattern library limited to lines/cross/solid.

---

## Appendix A — phase detail from the sixth revision (phases 10, 11, 18, 20, 21), still current

### Phase 10 — BIM (modules `datums`, `stairs`, `families`, `relations`, `checks`; dialogs in `project_ui.ts`)

- **Levels** (Insert → Levels): add, rename, change elevation (metres) and delete. A level is a named elevation in the project manifest (neither SGEO nor SDRW has one); every element refers to its level by id and its geometry is regenerated from it, so **moving a level rebuilds everything on it**, or is refused with the reason. A level that elements stand on cannot be deleted. Adding a level can make its floor plan. Undoable like any command.
- **Grids** (tool G, Insert → Grid): a named line in plan, stored as an SGEO group without geometry (no volume, so never in quantities or 3D), drawn on every plan as a dash-dot line with a bubble and label.
- **Stairs** (Insert → Stair, two clicks): straight flights between two levels with offsets. Stored as parameters only; the solid (monolithic flight, exact treads and risers) is regenerated, and the plan symbol (outline, tread lines, arrow up) is derived on every read. `n` risers give `n − 1` treads. **Only geometric limits are checked (it must rise; riser and tread must be sensible lengths). Building-code rules are not checked.** Straight flights only: no landings, turns or spiral stairs.
- **Families and generic model objects** (Insert → Families, Family tool): built on **SGEO's own family mechanism**, no second format. A family has a shape (box, cylinder, sphere) and typed parameters with default, bounds and optional **formulas** (derived parameters, dimension-checked by SGEO). Instances override parameters; editing the family regenerates every instance atomically or refuses and names the instance that would break a bound. SPADRA adds only where an instance stands (level, point, rotation, height). Lengths are shown in the project unit and converted at the edge. **Not built:** family files that can be imported or exported on their own, nested families, shapes beyond SGEO's three generators.
- **Relationships** (`relations.py`, shown in Properties as links): level, host wall, hosted doors / windows, family, room membership, bounding walls, the views an element appears in, the sheets those are on. Everything is read from data the project already holds; **nothing is stored twice and nothing is inferred**. A link is shown only when its target exists; a missing host reads "(missing)". Clicking a link opens the view / sheet, selects the element, or opens the families dialog.
- **Custom properties**: name / value pairs on any element, stored in the element's SGEO metadata under `custom:`, so they travel through save, copy, GLB / SGEO export and import. Remove with ×.
- **Schedules**: door, window, wall, floor, roof, beam and column, and room schedules (earlier) plus the project-wide **element count** and **material quantities** (used by reports). Schedules follow edits and deletions.
- **Coordination checks** (`checks.py`, Analyze → Check model): duplicates (identical elements at the same place), tag associations, and **geometry conflict candidates**. Every finding has a category and severity; the dialog shows counts, "Show" buttons that select the element, and **what the check does not do**. **The conflict test compares axis-aligned bounding boxes** (overlap of more than a few millimetres in all three directions). It finds real clashes and also over-reports boxes that merely enclose each other (a table in a doorway lies inside the wall's box), so findings say "candidate" and the person decides. Walls that meet at corners are not tested against each other. **There is no exact solid intersection, and none is claimed.**
- **Rebuild** (Analyze → Rebuild): regenerates every element from its parameters as a background operation; one undo step.

### Phase 11 — coordinated 2D / 3D documentation (`annotations.py`, `sheets.py`, `sheet.ts`)

- **Stable ids**: every element, view, sheet, viewport and tag has an id that never changes; views are derived from the model each time they are read, so a section, elevation or schedule **cannot disagree with the 3D model** (earlier) and now also shows stairs, grids, rooms, columns and families (on plans and sheets).
- **Tags** (Annotate → Tag, two clicks: pick the element, place the text): a tag is an ordinary SDRW `text` entity whose metadata names the element and the field it shows (mark, name, type, size …). **Its text is re-derived inside the same command that changes the model**, so one Undo puts element and tags back together. Only tag entities are ever rewritten; every other note, dimension and line is the person's own and is never touched. If the tagged element is deleted the tag keeps its last text, is marked **detached**, appears in the status bar and in the model check, and re-attaches on Undo.
- **Sheets**: title block drawn from the project (name, client, number) and the sheet's own fields (sheet number, name, scale, paper, drawn / checked by, date, status, revision); **sheet notes** (text on the paper); **revision table** (newest first) and **Issue revision** (Properties, or the Revision tool). **Issuing records a digest of everything the sheet shows**; later, the digests recomputed from the live model say exactly which viewports **changed since the issue**. Nothing is guessed: a changed digest is a changed picture. The Project Browser marks a changed sheet, the sheet shows a screen-only "Changed since revision …" marker, the status bar shows "N sheets changed" with a menu that opens them, and reports include it.
- **Plan / section / elevation / schedule on a sheet** (Place View), each at its own scale (`vp:<id>:scale` in Properties). Example verified in the tests: Level 01 plan + a section + an elevation + a schedule on sheet A101.
- **Printing** (Print / PDF, or Ctrl+P): the sheet is printed **alone, whole, at the size of its paper, in light colours, whatever the zoom or theme on screen**. Verified by producing a real PDF in Chromium: one page, page size within 1.5 mm of the paper (A1 landscape, 841 × 594 mm), text extracted from the PDF contains the sheet number, name and title block, and none of the application chrome. (Found while testing: printing used to print the whole application at the on-screen zoom; fixed.) **There is no server-side PDF writer** (reportlab is not installed here); PDF comes from the browser's print dialog.
- **Not built:** sheet dimension and legend tools, viewport cropping, editing the title block layout, and exporting a sheet to DXF / DWG / SVG.

### Phase 18 — import / export (`interop.py`, `jobs.py`, `interchange.ts`; library changes below)

| Format | Import | Export | State in this tree |
| --- | --- | --- | --- |
| SGEO (model) | yes | yes | **native**: identity, parameters, hierarchy, families and metadata kept; levels matched by name |
| SDRW (drawing) | yes | yes | **native**: layers, line types, blocks kept |
| SLDC | – | yes (reports) | written and validated by `src/utils/sldc` |
| GLB | yes | yes | native adapter, tested with real files |
| STL | yes | yes | native adapter, tested with real files (binary) |
| **OBJ** | **yes** | **yes** | **new native adapter (below), tested with real files** |
| SVG | yes (strict subset) | yes | native adapter, tested |
| DXF | yes | yes | adapter written, **needs `ezdxf`, not installed here: not run** |
| IFC | yes | – | adapter written, **needs IfcOpenShell: not run** |
| DWG | – | – | needs an ODA converter: **not available, not run**; offered as unavailable with the reason |
| STEP | – | – | needs Open CASCADE: **not available**, shown as unavailable |
| PDF | – | – | **no PDF writer or reader installed** (see sheets: browser print) |

- **Inspect before importing**: choosing a file shows what is in it before anything changes: its **real type read from its content** (not its name), whether it parses with the adapter that would import it, version, declared unit (or that it is assumed), counts, overall size, and anything suspicious. The Import button is disabled for a file that cannot be imported, with the reason. **The inspection and the import cannot disagree**: both run the same adapter and the parsed result is cached (3 files, ≤ 32 MB) and reused.
- **Conversion report** after every import and export, with the same keys everywhere: direction, status (complete / partial / failed), adapter, accuracy class, source, destination, **converted, lost, approximated, unsupported**, errors, warnings, assumptions. Mesh formats report "partial" because they drop materials / hierarchy / identity; SVG reports that reimport ignores styling. **"Show what will be lost"** in the export dialog asks the server for the exact report *before* anything is written.
- **Imports are one undoable command** (one revision, validated and rolled back like any other change) and run as a **background operation** (below). A file with an unsupported unit asks for one (mm, cm, m, in, ft): wrong units scale everything, so the dialog says so, and the assumption is recorded in the report.
- **Round trips actually run** (`test_exchange`, 42 tests, `test_interop`, `interop.e2e`): SGEO → GLB / STL / OBJ → SGEO (geometry compared, loss reported), SGEO and SDRW native round trips (identity preserved), SVG round trip, an STL cube of known size imported in mm and read back as 1 m, undo of an import in one step, damaged / mislabelled files refused with a readable message and **no change to the project**.
- **Library change (SLAI `src/utils/spatial_formats`)**: a **Wavefront OBJ adapter** (`adapters/obj.py`) — see "Library audit".
- **Not done:** DXF / IFC / DWG round trips on real files (libraries missing); conversion fidelity for IFC beyond what the adapter documents; PDF; STEP.

### Phase 20 — properties and Project Browser (`browser.ts`, `properties.ts`)

- **Project Browser**: Views (3D, floor plans, sections, elevations, schedules), Models, Drawings, Levels, Families (with instances), Categories (every element, by category), Sheets, Scenes, Animation assets, Reports. Search; **groups open and close without activating the row**; the row of the current selection is **revealed** (groups open to it); changed sheets are flagged. **Keyboard**: arrows move, → / ← open and close groups, Home / End, and the **tree is exposed to assistive technology** (tree / treeitem roles, levels, expanded and selected state). **Menu** (right click, or Shift+F10 / the menu key): Open / Select, and **Delete** where the server has a command that deletes that kind of row (report, family, element, sheet, section / elevation / schedule). Deleting asks first, says it can be undone, and a sheet with issued revisions says it will be deleted with its history.
- **Properties**: groups with edit-in-place fields that send one command each; **link rows** (above); **custom properties** (add / remove); **sheet notes**; **tag field** choice and a link from a tag back to its element; sheet title-block fields; viewport scale; an **Issue revision…** action. A refused edit puts the field back to what the project holds. Row ids are stable (`prop-<key>`).

### Phase 21 — interaction, background work, errors, accessibility

- **Background operations** (`jobs.py`): import (model / drawing), export (model / drawing), report, check, rebuild and the beam simulation / optimisation run as jobs. **Heavy work runs off the request lock on a private snapshot** of the project; the result is applied as **one short command under the lock** and is rejected (with a message) if the project was closed or replaced meanwhile. States queued / running / done / failed / cancelled, progress 0–1 with a message, at most 2 run at once, the last 40 are kept. **Cancel is honest**: it is checked between steps, and once a job starts applying its change it can no longer be cancelled (the answer says so and points to Undo). A cancelled job has changed nothing (tested). The **status bar shows a progress chip** while something runs; **Operations…** (File menu) lists them with progress, cancel / dismiss and the error's kind and what to do; the list **survives a reload of the page** (the server holds it) and running jobs are followed again.
- **Errors in words** (`diagnostics.py`, `errors.ts`): every failure shows **what kind of problem it is, what happened and what to do next** (e.g. "Conflict: A view named 'South' already exists. Choose another name."), from a category table the **server serves once** in the start-up data (the client holds no copy). Unexpected failures say they are unexpected, name the cause, and promise nothing they cannot know. A **diagnostics log** (`diagnostics.log` in SPADRA's data folder, rotated at 512 KB, one JSON line per failure: when, where, code, message, project name and revision) is shown by **File → Diagnostics…** with **Copy**. **It never holds project content, file content or anything typed** (tested with a marker string). Writing it never fails an operation.
- **Keyboard**: tool letters **V L P R O D T W G H M** (a tool that lives on another tab switches to that tab so the pressed tool is visible), **/** search, **2** / **3** switch 2D / 3D, **F** fit, **?** or F1 the list, arrows pan and **+ − 0** zoom the focused drawing (also the sheet), Shift+F10 the browser menu. Nothing fires while typing in a field or while a dialog is open. The shortcut dialog is checked against the handlers' source by a test.
- **Accessibility (measured by `accessibility.e2e`)**: **every visible control in the main views (3D, plan, schedule, sheet, on every menu tab) and in the dialogs (Levels, Families, New report, Check model, Export, Operations, Diagnostics, Project properties, Shortcuts) has an accessible name**; **text contrast is ≥ 4.5 : 1 (3 : 1 for large text) in the light, dark and high-contrast themes** on the sampled screens (plan, schedule, sheet, Analyze); **no sideways scrolling at 1672, 1280, 1100 and 900 px wide or at the largest text size (140 %)**, with the 2D / 3D switch always in reach; reduced-motion is honoured; the sheet and drawing have focusable canvases with a stated way to move them. (Found and fixed while testing: the Properties "Issue revision…" button and every in-panel `tool-inline` button had no style of its own and fell below the contrast ratio in the dark theme; muted text on the highlighted browser row was 4.36 : 1.)
- **Layout**: unchanged from your screenshots except as listed under Deviations. The tool strip scrolls sideways when there are more tools than fit; **the 2D / 3D switch and the side buttons stay pinned**. (Found and fixed while testing: adding tools had pushed the switch off the screen at 1672 px.)

### Library audit — `src/utils/spatial_formats` (SGEO, SDRW, adapters)

I went through what SPADRA calls in the library (every `import` from `src.utils.spatial_formats` and `src.utils.sldc` in `applications/SPADRA`) against what the library offers, with the SPADRA test suite and the real files made by the tests as the check.

- **What SPADRA needed that was missing and is now there: OBJ.** `adapters/obj.py` (new): text export (one `o` block per mesh object, global 1-based indices) and import (`v` / `f`, `o` / `g` object names; polygons of more than three corners are fanned into triangles — exact only for convex polygons, and the count is reported; normals, texture coordinates, `mtllib` / `usemtl`, smoothing groups, points and lines are **counted and dropped**; free-form curves and surfaces are **refused**). Limits: 64 MB, 2 000 000 faces, 4 000 000 vertices, 10 000 groups. Wiring: `adapters/exchange.py` (`scene_to_obj`, `obj_to_scene`, with the same conversion-report shape as GLB / STL), `adapters/__init__.py` (capabilities entry), `sgeo/document.py` (`export_obj`, `import_obj`, `to_obj`, `from_obj`) and `cli.py` (`.obj` in `convert`). README updated.
- **What the library already provided and SPADRA uses without change**: SGEO families, formulas and atomic regeneration; SGEO groups and metadata (the `spadra:` namespace); SDRW layers, line types, blocks, text entities and metadata (tags); GLB / STL / SVG adapters and their reports; SLDC packages (`make_table`, `make_block`, `new_package`, `dumps_package`, `inspect_package`, `to_markdown`).
- **What SPADRA keeps on its own side because the formats have no such concept**: levels (SGEO has none), the link between a tag and its element, sheets, title blocks and revision history (SDRW is a drawing, not a sheet set), and conflict candidates. They are stored in SPADRA's project manifest or in the metadata of ordinary SGEO / SDRW objects, so the native files stay valid for any other SLAI tool.
- **Known gaps, deliberately not patched**: the library has no PDF writer, no B-rep kernel, no exact solid intersection; GLB is float32; DXF / IFC / DWG / STEP need optional backends that are absent in the cloud tree. The shared-code change needed to save animation (`animation-v1` / `animation-v2` in `container/versions.py`) is unchanged and still needs your approval (see "On your machine").
- **Files changed in the library in this revision:** `adapters/obj.py` (new), `adapters/exchange.py`, `adapters/__init__.py`, `sgeo/document.py`, `cli.py`, `README.md`. They are delivered with SPADRA to `src/utils/spatial_formats/`, with a backup, and must be reviewed as shared code.

### Bugs found by this work and fixed

Real defects, found by tests or by measuring, not by reading:

- The 2D tool strip grew past the window and **pushed the 2D / 3D switch off screen** (every browser test timed out); the strip now scrolls and the switch is pinned.
- **Menus opened from a partly hidden button closed at once** (the browser scrolls the button into view after the click, and the shared menu closes on scroll); all SPADRA menus now open one frame later (`menus.ts`).
- The Properties panel's field ids had become unstable (`prop-width-7`); they are `prop-<key>` again.
- `GET /api/diagnostics?limit=100` answered 400 because a query string is text (an existing test passed for the wrong reason); fixed and tested with good and bad values.
- "Open view" and "Place view" menus listed a view once per place the browser lists it (Views, Levels, Drawings); they now list each once.
- The Operations list was lost on reload (it lived only in the page); it is read from the server.
- Printing a sheet printed the whole application at the zoom on screen (above).
- Contrast and an unstyled shared button (above); "1 entities" in a conversion report now reads "Entities: 1".

## Appendix A (continued) — earlier revisions, still current

### Assistant (15) — a pattern interpreter, **not a language model**

It recognises twelve request forms (create a wall, change a parameter, move, delete, section, animate, area / perimeter / volume / mass / distance, connected and nearby elements, explain, check dimensions, simulate and optimise a beam), reads quantities and units from them, builds a typed proposal, **validates it by a dry run on a scratch copy**, shows a before / after preview, and executes through the project's own commands after your approval, with read-back verification where it can read back, revision-safety (409 if the project moved on), Undo, a second confirmation for high-risk changes, and all-or-nothing multi-step proposals. Anything else is "Unsupported" and nothing changes. The SLAI Language Agent is **not** used (it needs PyTorch and trained checkpoints, which could not be imported where I built). Conversation and proposals are kept in memory only.

### Engineering workspace (12), agents (14), animation (13)

- Calculator: units, formulas and constraints computed by the **STEM Agent** (SI units with prefixes only; no inch / foot, no compound symbols). Measure: SGEO volume / area / mass / centre; Spatial Agent distances and bounding-box relations (boxes, not exact geometry).
- Simulation: one beam as a **single-degree-of-freedom** system (STEM stiffness, SGEO mass, RK4 integration by the Simulation Agent), checked against the closed-form solution; **not validated against measurement or finite elements, not a design check**.
- Optimisation: smallest beam depth / width within your bounds that keeps deflection under span ÷ ratio; the Optimization Agent searches, the STEM Agent evaluates; **no strength, buckling or code checks**.
- Agents (Analyze → Agents): all 25 SLAI agents with spec section, status and, when unused, the reason. Integrated: STEM, Simulation, Spatial, Optimization, Animation. **Not integrated:** Language, Planning, Reasoning, Knowledge, Alignment, Adaptive, Learning, Safety, Evaluation, Perception (need PyTorch / statsmodels, not importable here), Network, Verification (formal objects only), Reader (nothing to attach it to), Execution (every change must go through validated commands), Provenance, Quality, Privacy, Handler, Collaborative, Observability.
- Animation: keys, poses, presets, interpolation (linear / smooth / step), markers, PNG-frame and WebM export; every edit is computed as the complete spec and evaluated by the real `AnimationAgent`. No camera tracks or hierarchy (not in the SGEO animation schema).

### Rooms, columns, quantities, project management, utilities

- Rooms (area / perimeter / volume computed from the outline; related elements by geometric tests) and columns (real solids); room schedule; Check model flags unenclosed rooms and invalid columns.
- Quantities: volume and surface area from SGEO; mass **only where a density exists** (steel from the engine; others typed by you; none assumed); CSV; works in every view.
- Projects: New / Open / Save / **Save As** / Close; backups (newest 5) written before each overwriting save; **Recover from a backup…** as a new unsaved copy after hash checks; Project properties; broken or newer projects refused; files changed outside SPADRA reported.
- `utils/` and `configs/` (errors, helpers, config loader, `spadra_config.yaml`, `spadra_plans.yaml`). **My interpretation, please confirm:** "plans" are *resource plans* (named limit profiles: `standard`, `compact`), not subscription tiers or feature gates.

### Security and negative tests

`tests/test_security.py` (12) over real HTTP: malformed / oversized / wrongly shaped bodies, another Host name, path traversal, NaN / Infinity, control characters, garbage GLB / STL / IFC / SVG imports, an SVG with an external entity, damaged projects, stale revisions (409), no tracebacks or server paths in errors, 401 without the session cookie.

## Appendix B — deviations from the screenshots

- Browser-window controls are omitted; the check toast reads "Model check".
- Added without a screenshot: the assistant panel, the timeline strip and Pose panel, and (this revision) the Levels, Families, New report, Report, Issue revision, Import, Export, Operations and Diagnostics dialogs, the job and documentation-sync chips in the status bar, and the Project Browser menu.
- **The tool strip scrolls sideways** when there are more tools than the window fits (at 1672 px the last tools, Elevation and Schedule, are partly cut off on the 2D Model tab until scrolled); the 2D / 3D switch and the side buttons stay in place. **Grid, Stair and Family are on the Insert tab** (and have the hotkeys G and Tag is under Annotate), not on the Model tab.
- Four right-side mode icons use glyphs of my own choosing.

## Appendix C — on your machine: what is unverified

What I could run on your computer is its **Linux VM** (Python 3.10, `jsonschema` 3.2.0), not your Windows Python. Everything about Windows is untested.

1. **`jsonschema` must be 4.x or newer** (`pip install -U jsonschema`); with 3.2.0, SLAI's `invariants.py` cannot import `Draft202012Validator`, which breaks **saving** and **Check model**.
2. **Saving a model that has an animation needs a one-line change in shared SLAI code**: `src/utils/spatial_formats/container/versions.py`, `FEATURES["0.2.0"]` must include `"animation-v1"` and `"animation-v2"`. Changed in the cloud copy only; apply deliberately, with a backup.
3. **Whether the agents import on your Python.** If the Animation or STEM agent does not, SPADRA says why and keeps those features off.
4. **DXF / IFC / DWG** work only if `ezdxf` / IfcOpenShell / an ODA converter are installed there; SPADRA reports what is missing. They were never run by me.
5. **WebM export** depends on your browser's `MediaRecorder`. **Printing to PDF** depends on your browser's print dialog (`Save as PDF`); verified in Chromium here.

## Other

- Nothing was committed, pushed or published.
- Earlier fixes (`README.md` name, five-agent audit) are unchanged; their backups are in `.slai-*-backup-*`.
