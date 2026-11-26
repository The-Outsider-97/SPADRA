# SPADRA: excluded-element cutting fix, Element Browser backend, docking engine
Date: 26-11-2026. Status: **partially complete**. Read "What is NOT done" before relying on this.

## 1. Audit (what existed before)
- Void cutting policy lives in `cutting.py` (policy, `applies`, `classify`, excluded ids/categories). Floors, extrusions and walls get exact 2D cuts; other categories (stairs, beams, roofs) get non-destructive `sgeo.boolean` modifiers, tracked per element in `void_boolean_modifiers` {void id -> modifier id}.
- Relationships (groups, chains, links) live in `project.manifest["relationships"]`; hosted/derived elements cannot be linked.
- The client shell (`shell.ts`) is a fixed 3-column grid. There was no hierarchy panel and no dock/float/stack system.
- No SGEO stable topology ids, no void sub-object (vertex/edge/face) editing, no persisted hide/lock state.

## 2. Reported bug: excluded stairs still cut
**Cause found (verified by reproduction):** `_reconcile_boolean_voids` (forms.py) trusted only the metadata map. A modifier that existed but was not in the map (e.g. after copy/array, undo/redo or reopen) was never removed even when the element was excluded, and copies could be left uncut.
**Fix:** reconcile now reads the *actual* modifiers on each element, adopts void modifiers missing from the map, removes duplicates, and removes any modifier whose void is excluded/not applicable; it only writes metadata when it changed.
**Tests:** `ExcludedElementsAreNeverCut` (8 cases: excluded stair, category exclusion, all policies, untracked modifier, no doubling, copy/array, undo/redo, save/reopen).
**Caveat:** I could not reproduce it on your actual project and could not run these tests here, because they need `trimesh`, which is not installed in my environment. Please run them on Windows. If the stair is still cut afterwards, send me the .spadra folder.

## 3. Element Browser (backend only)
- `element_browser.py`: `build` (hierarchy of groups, chains, hosts, void-cutting relationships; search, filter, sort), `inspect`, `plan_drop` (validates group/link/attach/reparent drops before anything changes).
- `relationships.py`: new `check_link` and `structure`.
- `routes.py`: three HTTP routes.
- Tests: `tests/test_element_browser.py`, 27 tests, all pass, including over HTTP.
- **Measured (this VM, 300 walls, 307 tree nodes):** build 22 ms, search 22 ms, sort 21 ms, inspect 31 ms, peak memory 0.4 MB, JSON 170 KB. Creating 300 elements took 29 s (existing project-command cost, not the browser). 600+ elements could not be created within the 80 s limit, so **900/2100-element numbers were not obtained**.

## 4. Docking engine (model only)
- `src/client/dock_layout.ts`: pure engine for dock/tab/stack/float/resize/collapse/close/reopen/reset, validation and recovery of damaged saved layouts, offscreen/missing-monitor repair, workspace persistence, named layouts, plug-in panel changes.
- `tests_ts/dock_layout.test.ts`: 21 tests, 21 pass (includes 500 random operations staying sound). Whole project typechecks.
- Two bugs found by the tests and fixed: a panel listed twice in one saved stack stayed in two places; recovery renumbered ids so a round trip was not exact.

## 5. What is NOT done
- Void sub-object editing (vertex/edge/face, 2D/3D, gizmos, snapping), additional void creation/cutting modes, SGEO stable topology ids and remapping, diagnostics beyond existing.
- Persisted hide/isolate/lock and their enforcement.
- Element Browser UI (tree, virtualization, drag and drop, context menus, selection sync).
- Wiring `dock_layout.ts` into `shell.ts`/CSS, native and multi-monitor windows.
- Browser/end-to-end verification (no browser available here).
- The 11 trimesh-dependent void tests error here (10 missing `trimesh`, 1 follow-on `None` volume); they need a run on Windows.

## 6. Test commands
- Python: `python -m unittest applications.SPADRA.tests.test_element_browser applications.SPADRA.tests.test_void_cutting`
- Docking: see the compile/run recipe in the session notes (tsc, then `node --test`).
