# SPADRA
Spatial Parametric Authoring, Design & Engineering.

Intelligent CAD, BIM & Engineering powered by SLAI

## Install and run SPADRA on Windows

**Starting from an empty computer? Follow these steps in order.** `python -m applications.SPADRA --check` only checks an installation; it does **not** clone repositories, install dependencies, or build SPADRA.

SPADRA currently runs **inside an SLAI source checkout**, not as a standalone Windows installer. Its Python server imports SLAI's shared `applications.tools` modules, and its web client uses SLAI's shared UI/build files. You therefore need **both** repositories, with SPADRA at `SLAI/applications/SPADRA/`.

### 1. Install prerequisites

- [Git for Windows](https://git-scm.com/downloads/win) (`git --version`).
- [Python 3.12](https://www.python.org/downloads/) or Python 3.10/3.11 (`py -3.12 --version` for the commands below). **Do not use Python 3.13** for this setup: SLAI's current `pyproject.toml` declares `>=3.10,<3.13`.
- [Node.js](https://nodejs.org/) **20 or newer**, including npm (`node --version` and `npm.cmd --version`).
- Internet access for cloning and installing packages.

After installing these tools, open a **new** PowerShell window and verify:

```powershell
git --version
py -3.12 --version
node --version
npm.cmd --version
```

If you installed Python 3.11 instead, replace `py -3.12` with `py -3.11` in the commands below.

### 2. Clone SLAI and SPADRA into the correct folders

Choose where you want the source code stored. This example creates `Projects` under your Windows user profile; change the location if desired. **These commands are for a fresh installation**, not for overwriting an existing checkout.

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Projects" | Out-Null
Set-Location "$env:USERPROFILE\Projects"

git clone https://github.com/The-Outsider-97/SLAI.git SLAI
Set-Location .\SLAI
git clone https://github.com/The-Outsider-97/SPADRA.git .\applications\SPADRA
```

The resulting layout must include:

```text
SLAI/
  pyproject.toml
  src/
  applications/
    tools/          (shared SLAI backend and web build)
    SPADRA/         (this repository)
      README.md
      requirements.txt
      package.json
```

If you **already have** an SLAI checkout (for example `G:\GAIA\AI\SLAI_v2.4`), do **not** clone SLAI again. Instead, open PowerShell in that checkout and clone SPADRA only if `applications\SPADRA` is not already present:

```powershell
Set-Location 'G:\GAIA\AI\SLAI_v2.4'  # replace with your actual SLAI path
git clone https://github.com/The-Outsider-97/SPADRA.git .\applications\SPADRA
```

If the SPADRA directory already exists, **do not run this clone command into it**; use your existing checkout and preserve your local changes.

### 3. Create a Python environment and install packages

Run these from the **SLAI root** (the directory containing `pyproject.toml`):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\requirements.txt
python -m pip install -r .\applications\SPADRA\requirements.txt
```

The first requirements file installs SLAI's dependencies; the second installs SPADRA's. SLAI's complete requirements set includes substantial optional AI/scientific dependencies, so installation can take time and may require platform-specific prerequisites. Optional SPADRA DXF/IFC support has its own `applications/SPADRA/requirements-interop.txt`; it is **not required to start**.

If PowerShell blocks activation, you can use the environment's Python directly instead of changing your execution policy: replace subsequent `python` invocations with `.\.venv\Scripts\python.exe` while at the SLAI root.

### 4. Install and build the web client

```powershell
Set-Location .\applications\SPADRA
npm.cmd ci
npm.cmd run build
Set-Location ..\..
```

`npm.cmd ci` uses SPADRA's committed `package-lock.json`. The build requires the shared SLAI files in `applications/tools/`, which is why cloning SPADRA alone is insufficient.

### 5. Check the installed application, then launch it

With PowerShell still in the **SLAI root** and the Python environment active:

```powershell
python -m applications.SPADRA --check
python -m applications.SPADRA --port 8771
```

The first command reports missing prerequisites and may report optional-agent warnings. It **does not fix them**. Only start the second command after resolving any blocking errors. The second command starts SPADRA on your own computer and normally opens a browser. If the browser does not open, use the **complete localhost URL printed by the server**, including its access token. Press `Ctrl+C` in PowerShell to stop the server.

SPADRA is currently a locally hosted browser application: this process does **not** install an `.exe`, create a Windows desktop shortcut, or publish a public website.

### Common installation problems

| Symptom | What to check |
| --- | --- |
| `No module named applications` | Run `python -m applications.SPADRA` from the **SLAI root**, not inside `applications/SPADRA`. |
| `git clone` says the destination exists | Use the existing checkout. Do not overwrite its files or local changes. |
| Python version warning or incompatible packages | Check `python --version`; use Python 3.10, 3.11 or 3.12, as declared by SLAI. Recreate the virtual environment with a supported interpreter if needed. |
| `npm.cmd` not found | Install Node.js 20+ with npm, reopen PowerShell, and check `npm.cmd --version`. |
| Web client missing or outdated | From `applications/SPADRA`, run `npm.cmd ci` followed by `npm.cmd run build`. Make sure `applications/tools` exists. |
| An agent is unavailable in `--check` | Check whether it is a warning for an optional capability or a blocking dependency error; use the diagnostic output to identify the missing component. |
| Port `8771` already in use | Stop the other server or choose a different port, such as `--port 8772`. |

For command options, run `python -m applications.SPADRA --help` from the SLAI root. Developers can use `python -m applications.SPADRA --dev` to rebuild the client automatically when its sources have changed.

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

**First-time users:** complete [Install and run SPADRA on Windows](#install-and-run-spadra-on-windows) above before using these commands. Everything runs in the Python environment that is active; SPADRA never creates, activates or switches one.

| Command (from the SLAI checkout) | What it does |
| --- | --- |
| `python -m applications.SPADRA` | Starts SPADRA on loopback (default port 8770) and opens the browser (`--no-browser` to skip). Refuses to start, with the reason, if Python, a required package, the SLAI checkout or the built web client is missing. |
| `python -m applications.SPADRA --check` | Reports Python, environment, SLAI checkout, packages, web client and the agents SPADRA uses, then (for information only) which of SLAI's other agents import on this computer, and exits 0 only when SPADRA can run. |
| `python -m applications.SPADRA --build` | Builds the web client with the shared static build (`npm run build`; on Windows `npm.cmd`) and checks that the output exists. |
| `python -m applications.SPADRA --dev` | As the first row, but rebuilds the web client first when it is missing or older than its sources. |
| `python -m applications.SPADRA.bundle` | Writes a **source bundle** (zip) of SPADRA, the shared tools and the SLAI libraries it uses, with the built client. It is not an executable or installer: unpack it and run the first row with a Python that satisfies `requirements.txt`. |

Python packages: `applications/SPADRA/requirements.txt`; optional file exchange: `requirements-interop.txt`. Web client: Node.js 20 or newer, then `npm install` and `npm run build` in this folder.
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
  computed exactly. A **floor** or solid **extrusion** is cut by every void that spans its full thickness: wholly inside its outline the void makes a hole,
  across or along its edge it takes a notch out of the outline, and voids that overlap each other act as one (their union; a polygon boolean in `planar.py`,
  checked against the definition at thousands of random points). A **wall** gets a rectangular opening wherever a void passes square through its whole
  thickness: a rectangle, or any outline whose part inside the wall is such a rectangle (an L-shaped void with its arm through the wall); it may start at the
  ground, reach the top or cut the wall in two, and voids that overlap in a wall cut one opening between them. Anything else cuts nothing, and Properties and the
  model check say which void cuts nothing and why: a void that would split a floor or extrusion into separate pieces, or leave nothing of it, or meet another
  outline at a single point (when several voids cannot all be applied they are applied in model order and the rest are named); a round or slanted void through a wall.
  Roofs, beams, columns, foundations and sloped or curved cuts are not cut.
  A void's Properties has a **Cutting** section (Cut Intersecting Geometry on or off; a policy of *every intersecting element* or *selected targets only*, with the
  targets you add) and **Diagnostics** that count what it cut, what it reached but cannot cut (reported as *not supported*, never faked) and what it was told to
  leave alone. No pockets or partial-depth cuts (a stepped solid would need faces that match along every step, which is not computed).
* **Sweep** (on a plan): click a path, choose a rectangular or round profile (or give any section through the command); corners are mitred. The path stays at
  one height: it does not climb or curve (a polyline approximates a curve), and a corner sharper than a 4-thickness mitre or a profile wider than the path can turn is refused.
* **Array** (Insert, Model or Modify; from the plan and the 3D view) repeats the selection and **keeps the pattern**: the array is an element of its own (Project
  Browser, Arrays) that remembers the source, the count, the spacing and the directions, and regenerates its copies when you change them in Properties.
  Rectangular arrays take X, Y, Z or any mix (XY, XZ, YZ, XYZ), each with its own count and spacing (a direction can also be any angle in plan or a vector through
  the command); a radial array takes a centre, an angle and a count. **Linked** (the default): a change to the source (its size, its position, the doors and
  windows of a wall) reaches every copy that still follows it. A copy you change by hand becomes **independent** (it is left alone and keeps its place in the
  pattern); a copy you delete leaves its place empty; lowering the count never deletes an independent copy. **Release array** keeps every copy as an ordinary
  element and drops the pattern; deleting the array deletes the copies that still follow it; deleting the source dissolves the array and keeps the copies. A wall
  brings its doors and windows; a door, window, room, grid line or another array cannot be arrayed, and neither can an item of an array (edit that array). One
  array holds at most **300** copies: SGEO checks the whole scene on every edit, so the time grows with the square of the size (measured: 25 items 0.15 s, 100 items
  2.1 s, 196 items 7.6 s to make; a source change that rebuilds 196 copies took 19.8 s).

## Foundations

**Foundation** (Model ribbon, Insert menu; select walls or columns first to stand them on): a **pad** (poer), **strip** (strook) under a wall, **foundation
beam** (funderingsbalk), **raft** (plaat) or **ground-bearing slab**, each with an optional build-up of layers above and below (concrete, insulation, sand,
gravel …, with thickness and conductivity), declared reinforcement (bars: diameter, count, role) and concrete class. A strip follows its wall and re-cuts its corners
with the strips beside it; a pad follows its column. The plan draws the foundation dashed, a section hatches it layer by layer, and the Foundation Schedule and
**Foundation quantities** give concrete volume, formwork (edge area), the layers, the R-value of the build-up and an **indicative** steel take-off. Checks report a
gap or overlap with the support's base, a pad that does not cover its column, a strip narrower than its wall, a lost support and overlapping foundations.
**This is geometry and quantities, not structural design:** nothing checks bearing capacity, settlement, reinforcement design or a building code, and the steel
figure has no laps or hooks. Not modelled: raft edge thickening, stepped or sloping footings, piles and pile caps, holes in a raft; a round pad is a 48-sided
polygon; a foundation does not follow a change in the *height* of its support (the check reports the gap).

## Grid lines

A grid line is one element shown on every plan, and in sections and elevations it crosses (as a dashed vertical line with its bubble). Select it in the plan:
drag an end grip, click the length beside it and type a length, or change Start, End, Length, Angle, Label and Bubbles (start, end, both, none) in Properties.
Labels are unique; a request that would give two grids one label, or a grid shorter than 10 mm, is refused whole. Grid lines are snapped to, and where two cross
they give an intersection snap. In a section a grid is shown where it crosses the cut plane (if that is on the cut line); in an elevation, at its end nearest the
viewer; a grid parallel to the picture is not shown.

## Roofs, gutters, steel profiles and sheet contents

* **Roofs:** flat, shed, gable and hip. Change the type, the pitch (degrees) or the slope (percent) in Properties; shed, gable and hip roofs need a right-angled
  four-corner outline.
* **Gutters and downpipes:** select a roof, then Gutter (half-round, ogee / K-style, box or fascia) hangs gutters on the eaves you choose (all of them, or the edges
  you pick; a preview shows what a request would make). Edges that follow one another become **one connected run** with mitred corners and capped free ends, falling toward the
  end, start, middle or corners you choose, with a downpipe at each low point; select a gutter, then Downpipe to add more. They follow the roof, and a roof edit that would
  remove an eave a gutter hangs on is refused whole. **Drainage** shows, for each gutter and downpipe, the roof's plan catchment area times a rainfall intensity you can edit
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

## Links, groups, attachments, measurements, property transfer and pictures

* **Link / Chain / Unlink** (Modifier ribbon, or right-click an element): an element has at most one transform-parent. Moving or rotating a parent moves its children by
  the same transformation, down the whole chain, in a fixed order; a link that would make a loop is refused, and linking to a new parent re-parents. Unlinking in the
  middle of a chain keeps the rest of the chain. Linked elements are tinted by role (parent, child, both) and the parent-to-child arrows are drawn in the 3D view and on the plan.
* **Group / Ungroup**: a group (2D or 3D, nestable, named) is selected, moved, rotated and copied as one; its members keep their own identity and geometry (nothing is
  merged), and a member that is also linked is not moved twice. Ctrl-click picks one member without the group.
* **Attach / Detach**: attach elements to a host so they follow it; Detach can keep the place or restore the earlier one. The Relationships panel lists every link,
  group and attachment and the chain an element belongs to.
* **Measure** (Analyze ribbon, key U, 3D view and plan views): in a plan, pick points (they snap to wall corners, ends and the grid) for a distance, length, angle, area,
  perimeter, radius or coordinates. In 3D: distance, horizontal and vertical distance, length (edge or path), angle, radius, diameter, area, perimeter, volume, thickness,
  clearance, coordinates and slope. Picks snap to corners, edges and faces; the unit and number of decimals can be chosen. The figures are worked out exactly from the
  model's geometry on the server; where the geometry is ambiguous (a non-planar face, points that are not on one circle, an open solid) SPADRA says so instead of
  giving a number. A result can be kept: it is stored in the project, follows the elements it refers to, and shows "lost" when one of them is gone.
* **Mirror Element** (right-click an element, Modifier ribbon, or the Mirror tool on a plan with elements selected): mirrors elements across a vertical plane given as a
  fixed X, a fixed Y or two plan points, keeping the originals (a mirrored copy) or flipping them. A wall keeps its doors and windows with their hinge sides swapped, a
  roof keeps its slope, columns, beams, grids, floors and rooms follow; shapes that are not the same when mirrored (stairs, handed profiles) are refused and left as they
  were, and each element is reported. Copies join the groups and links of their originals.
* **Copy / Mirror / Paste Properties**, and **Copy / Paste Element**, are separate entries in the context menu. Properties are copied between existing elements of a
  compatible kind, by group (general, dimensions, construction, appearance, formulas, classification, custom, modifiers, connections); no geometry is duplicated, and identity, placement,
  host and level stay as they were. Mirror Properties swaps sides by meaning (left/right, hand of a door, which end), or across a reference plane given by two plan points (along
  the element's length: its sides; square to it: its ends; any other angle is refused). Modifiers copy chamfer and bend (a bend turns the other way when mirrored); connections
  copy a wall's top and base attachment and a roof's end connections. Every target is reported as applied, skipped
  or failed, and a target that fails is left unchanged.
* **Save as a picture** (Snapshot menu): an off-screen 3D render, independent of what the viewport is showing, at 1080p, 1440p or 4K or a custom size, with
  anti-aliasing, shadows, ambient occlusion and tone mapping, as PNG or JPEG, with progress and Cancel. It never changes the view or the project.

Details and limits: [docs/RELATIONSHIPS_MEASURE_TRANSFER.md](docs/RELATIONSHIPS_MEASURE_TRANSFER.md).

## Not implemented or not verified

* DXF and IFC need `ezdxf` and `ifcopenshell` (`requirements-interop.txt`), STEP needs Open CASCADE (`OCP`) and DWG a separately licensed ODA converter; where one
  is missing SPADRA reports the format as unavailable instead of producing a file. IFC is import-only, STEP does not convert a building model, and there is no PDF
  writer (sheets print from the browser). DXF and IFC conversion, the Windows runs and the saved-project tests are defined in `.github/workflows/spadra-ci.yml`
  and have **not been run yet**: the environment these changes were made in had none of those libraries, no Windows and an outdated `jsonschema`.
* The packaged result is a source bundle, not a desktop installer.
* Arrays: up to 300 copies each (the engine's cost is quadratic), radial about a vertical axis only, no nested arrays, and a copy's identity changes when its source
  changes (a tag or animation track on a copy is lost then; it is kept when only the count or spacing changes). Foundations, grids, gutters and voids: see their
  sections above and `docs/SPADRA_completion_report.md` for what each does not do.

* Links, groups, measurements, property transfer, Mirror Element and snapshots: see the limits in [docs/RELATIONSHIPS_MEASURE_TRANSFER.md](docs/RELATIONSHIPS_MEASURE_TRANSFER.md).

## Specifications, reconstruction and impact previews

See [docs/SPEC_RECONSTRUCTION_IMPACT.md](docs/SPEC_RECONSTRUCTION_IMPACT.md): constraint-aware design specifications, a typed operation graph, dimension-aware 2D-to-BIM reconstruction (Model → Reconstruct), and live impact analysis in the modifier dialog.
