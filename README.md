# SPADRA
Spatial Parametric Authoring, Design & Engineering.

Intelligent CAD, BIM & Engineering powered by SLAI

| Component | Name | Capabilities |
| --- | --- | --- |
| Application | SPADRA | |
| 2D drawing format | .sdrw | Technical drawings, dimensions, constraints, layouts and annotations |
| 3D geometry format | .sgeo | Parametric geometry, solid modeling and assemblies |
| BIM | .sgeo | Building components, families, schedules, sections and coordinated documentation |
| Visualization | |	Materials, lighting, cameras and rendering |
| Animation | |	Walkthroughs, object animation, keyframes and timelines |
| Engineering | |	Calculations, analysis, optimization and verification |
| AI assistance | |	Context-aware, multi-agent design intelligence through SLAI |
| Documentation | .sldc | |
| Application identity | SLAI SPADRA |
| Software category | Parametric CAD and Spatial Design |


What makes SPADRA unique?
Its intended differentiator is the combination of:
- One coordinated workflow from drawing to model, documentation and animation.
- Native SLAI spatial formats.
- Context-aware specialist AI agents.
- Explainable and reviewable AI operations.
- Parametric design with deterministic constraints.
- Integrated engineering analysis and optimization.
- Consistent interfaces with SciCal, SReader, SLAI Writer and other SLAI applications.

## Run, check, build, package

Everything runs in the Python environment that is active; SPADRA never creates, activates or switches one.

| Command (from the SLAI checkout) | What it does |
| --- | --- |
| `python -m applications.SPADRA` | Starts SPADRA on loopback (default port 8770) and opens the browser (`--no-browser` to skip). Refuses to start, with the reason, if Python, a required package, the SLAI checkout or the built web client is missing. |
| `python -m applications.SPADRA --check` | Reports Python, environment, SLAI checkout, packages, web client and the five agents, then exits 0 only when SPADRA can run. |
| `python -m applications.SPADRA --build` | Builds the web client with the shared static build (`npm run build`; on Windows `npm.cmd`) and checks that the output exists. |
| `python -m applications.SPADRA --dev` | As the first row, but rebuilds the web client first when it is missing or older than its sources. |
| `python -m applications.SPADRA.bundle` | Writes a **source bundle** (zip) of SPADRA, the shared tools and the SLAI libraries it uses, with the built client. It is not an executable or installer: unpack it and run the first row with a Python that satisfies `requirements.txt`. |

Python packages: `applications/SPADRA/requirements.txt`. Web client: Node.js 20 or newer, then `npm install` and `npm run build` in this folder.
Tests: `npm run test:all` (type-check, TypeScript tests, Python tests, browser tests).

## Walls

Walls that meet at an angle are joined: each wall end that reaches another wall is mitered to the shared corner (or butted, for a T junction),
the joint is stored with the wall (`spadra:ends`, `spadra:joins`), and moving, rotating or deleting a wall refreshes its neighbours.

## Editing walls and other elements

* **Stretch and move.** Select a wall and drag the square grips at its ends (or click its length and type a new one); drag the wall itself to move it.
  Dragging an end onto another wall joins them. Every edit re-derives the joints with the neighbouring walls and the doors and windows the wall hosts, and
  one Undo takes it back.
* **Rotate.** The round handle above a selection, or Rotate (Q): walls, columns, floors, roofs, beams, rooms, stairs, grids, extrusions, voids, sweeps and
  generic models turn about a point you pick (a door or window turns with its wall). 2D drawing entities (lines, polylines …) do not rotate with this tool.
* **Draw walls in a plan.** The Wall tool works in plan views as well as 3D; the chain snaps to the ends and middles of other walls.
* **Section lines** are selected on a plan; drag their end grips to stretch them, drag the line to move it.
* **Grid snap** is a switch (View, or F9) that applies to the plan and to the 3D view; it is remembered in this browser.

## Automatic rooms

With **Auto rooms** on (Insert, on by default for a new project), a room is made for every space the walls close on a level, no roof needed, and it follows
the walls. The outline is the space inside the walls' faces. A room you have named or numbered keeps its name and number when the walls change, and a
room is removed when its walls open the space unless you have changed it (it is then kept and the model check warns that it is no longer enclosed). A room drawn
by hand is never touched, and drawing one over an automatic room replaces it. Limits: walls only (columns and beams do not enclose); a space with a separate
ring of walls inside it (an island) is skipped, not given a hole; the walls must meet within 2 mm.

## NL-SfB classification

The NL/SfB 2005 tables 0 to 4 (electronic edition 2019-12-23, built from the supplied spreadsheet by `data/build_nl_sfb.py`, 2231 rows) are shipped with SPADRA
(`data/nl_sfb.json`). Every element has an NL-SfB code (table 1; rooms use table 0), a construction method (table 2) and a material (table 3), chosen in
Properties by typing a number or a Dutch or English word. A code the table does not have is refused. Nothing is assigned on its own. The code appears as a column
of every schedule and can be shown in a tag.

## 3D navigation

Selecting an object makes it the centre of rotation (the "orbit" toggle on the 3D rail turns that off); the wheel zooms towards the pointer, Ctrl-drag looks around from
where you stand, the arrow keys orbit, Ctrl-arrows walk, and PageUp / PageDown move up and down.

## Extrude, Void, Sweep and Array

* **Extrude** (Insert or Model, on a plan): draw an outline, set its depth and base above the level in the strip above the plan. The outline is edited in
  Properties and the element moves and turns like any other.
* **Void** (same place) draws an outline that cuts, or **Make void** turns selected extrusions into voids (Properties has a Solid / Void switch). A void is drawn
  see-through in 3D and hatched on the plan, has no quantities and is left out of sections, elevations and GLB / STL / OBJ exports. What it cuts is only what can be
  computed exactly: a **floor** or solid **extrusion** gets a hole when the void lies wholly inside its outline and spans its full thickness, and a **wall** gets a
  rectangular opening when the void is a rectangle square to the wall that passes through its whole thickness (it may start at the ground, reach the top or cut the
  wall in two). Anything else cuts nothing, and Properties and the model check say which void cuts nothing and why. Two voids that overlap inside the same floor
  or wall are refused. Roofs, beams, columns and sloped or curved cuts are not cut.
* **Sweep** (on a plan): click a path, choose a rectangular or round profile (or give any section through the command); corners are mitred. The path stays at
  one height: it does not climb or curve (a polyline approximates a curve), and a corner sharper than a 4-thickness mitre or a profile wider than the path can turn is refused.
* **Array** (Insert, Model or Modify; works from the plan and the 3D view): copy the selection along a line (a step in x, y, z) or round a point (centre and angle),
  with a total number of items. The copies are ordinary elements; they are not a live group, so changing the original afterwards does not change them. A wall
  brings its doors and windows; a door, window or room cannot be arrayed alone.

## Roofs, gutters, steel profiles and sheet contents

* **Roofs:** flat, shed, gable and hip. Change the type, the pitch (degrees) or the slope (percent) in Properties; shed, gable and hip roofs need a right-angled
  four-corner outline.
* **Gutters and downpipes:** select a roof, then Gutter (half-round, ogee / K-style, box or fascia) hangs one gutter on each chosen straight eave; select a gutter,
  then Downpipe. They follow the roof. **Drainage** shows, for each gutter and downpipe, the roof's plan catchment area times a rainfall intensity you can edit
  (default 0.03 l/s per m²). That is arithmetic: it is **not** a sizing to NEN 3215 / EN 12056 and says nothing about whether the pipes are big enough.
* **Beam profiles:** HEA, HEB, HEM, INP, IPE, UPE, UNP, L (equal and unequal), lipped C, CHS, SHS and RHS, with a roll in quarter turns. The sizes are **nominal
  table values, not checked against a catalogue**, without root radii or tapers. The optimiser does not accept profile beams.
* **Doors and windows** swing about their hinge edge in the animation, as the 2D drawing shows.
* **On a sheet** (Place View): plans, sections, elevations, schedules, the **3D model** (vector shapes from a camera direction, follows the model) and
  **reports** (their text in a box); **Snapshot** in the 3D view puts a picture of the 3D view on a sheet (it does not follow the model).

## Connection to SLAI's commit contract

An agent's proposal in the assistant panel is only a proposal. It runs when a person approves it, through the same command system as every
manual edit (all steps or none, undoable), and the approval goes through SLAI's `CommitReconciliationStore` and the shared host in
`applications/tools/commit_host.py` (see `commits.py`). The only artifact is `project`; its revision is the project revision. Each decision is
stored in the project (`commits` in the manifest) under its idempotency key, so a repeated request returns the same receipt and never runs twice,
and a stale proposal is refused rather than applied. Requires the planning adapters in `src/agents/planning/`.

## Not implemented or not verified

* DXF, IFC, DWG, STEP and PDF export/import need `ezdxf`, `ifcopenshell`, `reportlab` or `OCP`, which were not installed where the tests ran; SPADRA reports
  them as unavailable instead of producing a file. Nothing was run on Windows.
* The packaged result is a source bundle, not a desktop installer.

