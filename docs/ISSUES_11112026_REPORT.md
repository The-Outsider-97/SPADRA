# Issue sheet 11-11-2026: implementation report

Scope: every item of `issues_11112026_SPADRA.pdf` (rainwater, roof edges, curtain walls) plus the SLAI agent refinement asked for in the accompanying
specification (section 8). Work was done on the code in `G:\GAIA\AI\SLAI` (applications/SPADRA, shared `src/utils/spatial_formats`), reusing the existing
command/reconcile/undo architecture, the SGEO model and the existing assistant proposal path. No new framework, geometry engine or agent layer was added.

Tests were run on a Linux copy of your tree (Python 3, Node 22, headless Chromium), not on Windows. Nothing was committed to your git repository: your
working tree already had many uncommitted changes, so the new and changed files are written in place and left for you to review and commit.

## 1. Rainwater (sheet item 1)

| Requirement | What was done | Where |
|---|---|---|
| Downpipe always centred on the gutter | The outlet sits on the centre line of the gutter section (gap + width/2) for every gutter type and roof turn; its top follows the gutter's real underside (curved for half-round/ogee). Moving or deleting gutter pieces slides the pipe back into the allowed span. | `gutters.pipe_frame`, `outlet_span`, `settle_after_delete` |
| A real hole visible in 3D | The gutter sheet is rebuilt with an exact circular bore cut through its bottom (planar boolean, closed solid), also next to mitres. A bug in the shared SGEO B-rep tessellator (faces with several holes) was found by these tests and fixed. | `gutters._sweep_with_holes`, `src/utils/spatial_formats/sgeo/brep_mesh.py` |
| Right-click to edit components; parametric | Right-click a downpipe: *Edit Components…* (list, reorder, replace, split, remove, open one in Properties) and *Add Downpipe section / Offset bend / Elbow / Shoe / Y-branch / Hopper head*. Double-click a fitting in 3D selects that one component; its own Properties rows (angle 72/45/40° or any 5–90°, direction, offset, length …) edit the assembly. | `gutters.edit_components`, `component_properties`; client `gutters.ts` (`downpipeComponentsDialog`), `store.ts`, `view3d.ts` |
| Several sections, edited separately | Any number of pipe sections; one takes the free length, the others are fixed; *Split* divides one. | `gutters.assembly`, `edit_components` |
| Connector automatic | A connector is generated wherever two sections meet (length and bend radius are assembly parameters). | `gutters.assembly` |
| Inherits NL-SfB / IFC | Every component reports the downpipe's (else the gutter's) NL-SfB code and its own IFC entity/predefined type (IfcPipeSegment, IfcPipeFitting BEND/EXIT/JUNCTION/ENTRY/CONNECTOR), and names where it came from; it can be overridden. | `gutters.component_classification` |
| Ground drainage, 3 types, parametric, auto-cut | Point drain (gully), linear channel drain and downpipe ground inlet. Placed from *Ground drain* (Insert/Model ribbon), from a floor's right-click (*Add Point/Channel Drain Here…*) or under a downpipe (*Add Ground Inlet Below*). Each finds the floor under it and cuts its opening through the floor's layers; the cut follows when the drain moves or the floor changes. | `gutters.add_drain`, `drain_cutters`, `reconcile_drains`; client `drainDialog` |

## 2. Roof edges and drip edges (sheet item 2)

| Requirement | What was done |
|---|---|
| Edge always parallel to the roof cut | The edge member's section is laid out in the cross-section of its edge with its inner face *on* the roof end as it is actually cut (plumb, square to the slope or horizontal), for every pitch, preset and plan rotation. Changing the cut or the pitch rebuilds it. (`elements.edge_section_frame`, `fascia_section`) |
| Right-click → add drip edge | Right-click a roof: *Add Drip Edge* (edge number or all). Right-click a roof-edge member: *Add Drip Edge on This Edge* (and the reverse from a drip edge). |
| Attached to the top face, following angle and layers | The drip edge's flange lies on the roof's top face, turns down parallel to the cut and wraps an edge member on the same edge. It is rebuilt when layers are added, the roof is re-pitched or turned, or the edge member changes; it is deleted with its roof. Parameters: flange, face, kick, kick angle, thickness, material. |

## 3 & 4. Curtain walls

| Requirement | What was done |
|---|---|
| Double-click a panel or mullion (2D and 3D) | 3D: the face under the pointer is mapped to the part it belongs to (every face of the curtain-wall mesh is tagged). Plan: the server picks the part at the plan's cut height. The part is selected and Properties shows that panel/member (type, material, thickness, door settings; member removal; corner post sizes). |
| Aware of walls at its ends | An end placed on a wall is hosted by it (stored), and the connection check reports a free end that lies close to a wall without being hosted. |
| Base on the floor's top face (also for walls) | A new wall or curtain wall stands on the top surface of the floor under it (200 mm above the level → base 200 mm up) and follows the floor when it is raised, lowered or thickened. Right-click: *Stand on the Floor Below* / *Free the Base from the Floor*. |
| Attach to a roof like a wall | Right-click: *Attach Top to Roof…* / *Free the Top*; the top member follows the roof slope. |
| Door opening angle editable in 3D | Select a curtain wall (or a door in a wall): a ◐ grip on each swinging leaf's free edge; drag it about the hinge (Shift = 15° steps). On release the angle is stored and the leaf is shown open (one undoable edit). |
| Draw several connected curtain walls, Enter to finish | New tool *Connected curtain walls* (plan): click corner after corner; Enter, double-click or right-click finishes (click the first point to close). One dialog sets the shared parameters and the corner type, then every segment is made as its own element, joined, and selected for editing. The existing *Curtain wall* tool (two clicks → dialog) and the 3D ribbon dialog behave exactly as before. |
| Corners like wall corners: L, Quad, Trapezoid, V | At a selected curtain wall's joined end the plan shows the same corner button as walls; it offers L-Corner, Quad Corner, Trapezoid Corner, V-Corner, Automatic and *Sizes…*. Also from right-click (*Corner at Start/End…*) and Properties. Each type is checked against the angle it is built for (L 45–135°, Quad 30–150°, Trapezoid 60–175°, V 15–165°); an impossible choice is refused with the reason, nothing changes. |

## 5. SLAI agent integration (specification section 8)

**Audit (8.1), from the live registry (`agent_hub.SPECS`, `HUB.status()`):** SPADRA used STEM, Simulation, Spatial, Optimization and Animation agents; all five
import and run here. Language, Planning, Reasoning, Adaptive, Learning, Evaluation, Safety and Perception need PyTorch, which is not installed in either
environment I could test; Knowledge needs statsmodels; Provenance fails on a missing `data` package. These stay unintegrated and say why — no trained model
was loaded or used anywhere in this work, and none is claimed. Model changes from agents already went only through proposals (dry-run on a scratch copy,
revision-bound consent, transactional run with undo); that was kept and reused rather than duplicated.

What was added:
- **Connection check** (`connections.py`, *Analyze → Connections*, `POST /api/connections`, and the assistant request “check the drainage / roof edge /
  curtain wall connections”). Analysis only: rainwater (pipe inside the gutter piece, bore fits the gutter, assembly builds, connectors between sections,
  free discharge, inlet wide enough for its pipe, drains hosted), roofs (edge member parallel to the cut and on it, drip edge on the top face and parallel,
  edge still exists, drip over a gutter), curtain walls (corner buildable at its angle, broken links, base not on the floor below, top not attached under a
  roof, free end near an unhosting wall, panels standing out of the frame). Every finding carries its numbers and the revision.
- **Proposal, never direct execution.** Findings with a correction can be ticked and *proposed*: one assistant proposal, dry-run first, bound to the
  revision; a finding or proposal from an older revision is refused (`Conflict`). Applying is the assistant's existing approve path with undo.
- **Spatial Agent for candidates only.** It is asked which drains/walls are near an element; each candidate is then confirmed with exact geometry. Without the
  agent the same candidates come from a scan and the result says so (tested to give identical findings).
- **Caching** per project revision (an edit invalidates it).
- **Observability**: every Spatial Agent call is now timed and its success recorded; `GET /api/agents/health` summarises calls, failures and latency per
  agent/operation, and the SLAI Observability Agent (now registered as integrated) summarises the same samples. Its incident level is *not* shown: it rates the
  synthetic trace it opens for a summary as an anomaly, which would be a false alarm.

Not done in this pass (honest limits): 8.4 parametric-dependency analysis was not extended beyond the existing constraint engine; STEM/Optimization calls
are recorded but not yet timed; no agent proposes component configurations by itself — corrections come from deterministic rules.

## 6. Verification matrix

| Item | Implemented | Automated tests | Seen working in a browser (e2e) |
|---|---|---|---|
| Downpipe centred + real hole | Yes | `test_downpipe_assembly` (centred for 4 types × 2 turns; ray test through the hole; closed solid), `test_gutters` | — (geometry checked numerically) |
| Downpipe components, connector, classification | Yes | `test_downpipe_assembly` | Right-click → *Add Offset bend*, part picked in 3D (`bimconnections.e2e`) |
| Ground drainage ×3, auto-cut | Yes | `test_ground_drainage` | Floor right-click → point drain, floor cut (`bimconnections.e2e`) |
| Roof edge parallel to cut | Yes | `test_roof_edge_cut` (3 turns × 4 cuts) | — |
| Drip edge (menu, top face, layers) | Yes | `test_roof_edge_cut`, `test_bim_ui_contract` | Roof and edge-member menus (`bimconnections.e2e`) |
| CW part editing 2D/3D | Yes | `test_curtainwall_connections`, `test_bim_ui_contract` (HTTP pick) | Double-click in plan → panel in Properties |
| CW/wall base on floor top; attach to roof; wall hosts | Yes | `test_curtainwall_connections` | — |
| CW door angle in 3D | Yes | `test_curtainwall_connections`, `test_bim_ui_contract`, TS `assembly` tests | Door grip dragged, angle stored |
| Connected CWs + Enter; old tool unchanged | Yes | `test_curtainwall_connections`, TS tests | Run drawn with Enter; `curtainwall.e2e` (old tool) passes |
| Corners L/Quad/Trapezoid/V | Yes | `test_curtainwall_connections`, `test_bim_ui_contract` | Corner changed from the context menu |
| Connection check, proposals, stale refusal, cache, agent fallback, health | Yes | `test_connections` (11) | Check → proposal pending in the assistant, revision unchanged |

## 7. Test results (Linux copy)

- **Python** (`unittest discover`, 1831 tests): all new and changed tests pass. 3 failures remain and fail identically on the code before this work:
  `test_animation_authoring` (door swing key), `test_parametric_workflow` (expects a Conflict the Animation Agent no longer raises) and `test_api.ModifierPreview`
  (needs `trimesh`, not installed here). One new failure found on the way (an import scanner reading a docstring line) was fixed.
- **TypeScript**: `tsc --noEmit` clean; 142/142 `tsx` unit tests (including 9 new ones that read the server's tables, so client and server cannot drift).
- **Browser end-to-end** (headless Chromium against the real server): the new `bimconnections.e2e` (9 steps) and the existing `curtainwall.e2e` (9) pass;
  all 32 suites were run. `accessibility.e2e` has 1 failure (light-theme contrast) that is identical on the code before this work. `walls.e2e` failed once
  (a timing-sensitive dimension step) while other suites ran in parallel, and passed when run on its own.

## 8. Known limits
- Floors are taken as flat: a base stands on the floor's top at its level + offset; sloped floors are not followed.
- A drain body shallower than its floor gets an opening through the layers it passes completely; the remaining recess is reported (warning), not cut.
- Component classification is computed and shown per component; IFC export of individual downpipe components was not verified.
- Plan double-click picks at the cut height (1 m above the curtain wall's base by default): to pick a transom or the top member use the 3D view.
- The 3D door grip is for hinged leaves; sliding doors and multi-panel doors in walls are edited in Properties.
- The connected-curtain-walls run is a separate tool so the two-click *Curtain wall* tool keeps its behaviour.

## 9. Files
New: `connections.py`, `src/client/assembly.ts`, `src/client/connections.ts`, tests `test_downpipe_assembly.py`, `test_ground_drainage.py`, `test_roof_edge_cut.py`,
`test_curtainwall_connections.py`, `test_bim_ui_contract.py`, `test_connections.py`, `tests_ts/bim_connections.test.ts`, `tests_e2e/bimconnections.e2e.test.mjs`, this report.
Changed: `gutters.py`, `elements.py`, `curtainwall.py`, `attachments.py`, `geometry.py`, `project.py`, `routes.py`, `forms.py`, `sfb.py`, `classifications.py`,
`agent_hub.py`, `assistant.py`, `spatial_bridge.py`, client `store.ts`, `view2d.ts`, `view3d.ts`, `shell.ts`, `gutters.ts`, `curtainwall.ts`, `types.ts`, `shortcuts.ts`,
tests `test_gutters.py`, `test_elements.py`, `test_agents.py`, shared `src/utils/spatial_formats/sgeo/brep_mesh.py`, and the rebuilt `dist/`.
The originals of every changed file are kept in `Claude outputs\_backup_11112026` next to the repository.

To rebuild and test on Windows: `cd applications\SPADRA`, `npm run build`, `npm run typecheck`, `npm test`, `python -m unittest discover -s tests -t ..\..`.

## 10. Follow-up: curtain-wall spacing errors ("two transoms are only 41 mm apart")
The message was right that nothing could be built there, but wrong to stop at that. Two changes:
- **Fewer false conflicts.** `bays()` now folds a spacing remainder too small to hold a panel (frame width + 50 mm) into its neighbouring bay, so "every 600 mm"
  over 3041 mm no longer leaves a 41 mm sliver member. The layout is only refused when a real pair of members (restored, moved or hand-added) cannot both fit.
- **A precise error with corrections.** `curtainwall.spacing_conflict` names the two members (direction, positions in mm), the gap, and what is needed. It lists
  fixes as `model.set_property` steps, and each is offered only after the modified spec is checked to build. The client's "This change cannot be built as it is"
  dialog (`store.ts` `offerFixes`, `errors.ts` `fixesOf`) shows one button per fix ("Instead: ..." or "..., then make the change") and "Show the element".
- Tests: `test_curtainwall_spacing.py` (7), the e2e step "spacing error offers fixes" (`bimconnections` now 10 steps). Linux totals: Python 1838 tests with the same 3
  pre-existing failures, TypeScript 143/143, `bimconnections.e2e` 10/10, `curtainwall.e2e` 9/9.
Files: `curtainwall.py`, `src/client/errors.ts`, `src/client/store.ts`, the tests above, rebuilt `dist/`. Originals: `Claude outputs\_backup_11112026_b`.

## 11. Follow-up: "The roof comes down into door field 2.1: a door needs its whole field"
The old message gave neither the place nor the reason. `curtainwall.door_conflict` now says which field (e.g. "field 2.1, 2nd from the start, 1st from the base"), how wide
and tall the door would be with the top following the roof, where the roof's underside is over that field (lowest to highest, in mm above the base), and by how much
the door would be cut. It offers corrections, each tried on a changed copy of the wall in both states (as stored, and following the roof) before it is shown:
- add a transom just under the roof's lowest point over the field (the door keeps its width; offered only when the stored wall is tall enough to hold it, otherwise the
  dialog says what is missing);
- move the door to the nearest bottom-row field the roof does not reach, carrying its door type, hinge, swing and opening angle (the cut door becomes a window first);
- make the field a window.
The same dialog as for spacing shows them ("..., then make the change" re-runs the attach). Also fixed on the way: a door in a top-row field was refused even where the roof
is level over it; it now stands up to the roof's level there. Tests: `test_curtainwall_roof_door.py` (7) and a bimconnections e2e step (11 steps now). Linux totals: Python 1845
tests with the same 3 pre-existing failures, TypeScript 143/143, `curtainwall.e2e` 9/9.
