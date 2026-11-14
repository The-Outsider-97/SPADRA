# Design specifications, operation graphs, 2D-to-BIM reconstruction and impact previews

What this adds to SPADRA, where it lives, how to run it, and what it does **not** do. Every figure below was produced by a command in "Reproduce" and can be re-run; nothing here is a claim that was not executed.

## 1. Constraint-aware design specifications (`design_spec.py`, `opgraph.py`)

A *specification* is data: typed parameters (units, formulas, ranges), constraints, relationships, a construction order, object ids, expected outputs. It is stored in the project (`spec.define`, revisioned) and run by the deterministic kernel (`spec.run`, one transaction, one Undo step). A model may *propose* a specification or a repair; the host validates it first and nothing is applied except through a command.

* Parameters are SGEO `ParameterGraph`s (existing system). Cycles, unknown references and unit errors are diagnostics, not exceptions.
* Constraints go to the shared engine (`common/constraint_engine.py`): the result is one of under-constrained, well-constrained, over-constrained (consistent), or conflicting, with the conflicting/redundant constraint indices.
* Operations (extrude, boolean, split, chamfer, offset, trim, void, bend, sweep) are typed contracts. Each reports `exact` / `approximate` / `unavailable` according to what this installation's kernel can do; an unavailable operation is a diagnostic naming the missing backend, never a fabricated result.
* Verification (`spec.verify`) runs the spec on a scratch copy and **measures** each expectation. `propose_repairs` offers parameter changes (or constraint removals, flagged "changes design intent") that were re-verified before being offered. `POST /api/specs/{id}/repairs/propose` turns one into an assistant proposal; only a person's approval applies it.
* Provenance is stored in `spadra:spec` metadata and survives save/load/SGEO/SDRW.

Not done: face references are descriptor-based (not topological names); no offset/loft/3D fillet/edge-bend kernel; no binding of a spec to a *family* parameter set.

## 2. Impact analysis and modifier preview (`impact.py`, `src/client/impact_panel.ts`, `dialogs.ts`)

`spadra.preview.v1`: `before`, `after`, `diff` (added/removed/modified), `predicted` (deterministic) with `audit` (prediction vs measurement), `dependents`, `collisions` (bounding-box *candidates*; "none" is a guarantee, "some" is a candidate), `diagnostics`, `valid`. A split plane is given in the object's **own frame** (the frame the SGEO kernel cuts in; for an extrusion its origin is the middle of its bounding box), and the prediction clips in that frame before reporting the world-space result. A refusal is either a case the predictor called empty, or a named kernel limit (below). A *learned* prediction is reported as `unavailable`; none is simulated. A refused preview is HTTP 400 with `details.analysis`. The modifier dialog shows the analysis live; Apply posts a toast with Undo. Valid parametric-family modifiers are not rejected; warnings are distinguished from invalidity.

## 3. Dimension-aware reconstruction (`reconstruct.py`, `src/client/reconstruct_ui.ts`)

Sources: raster (OpenCV + Tesseract), vector PDF (pdfplumber), SDRW drawing. Pipeline: acquire → dimensions (lines, ticks/arrows/extension lines, text) → scale calibration (median, MAD outliers, ratio of sums) → walls (parallel face pairing, collinear joins, openings with host/kind) → alignment (dimensions become `distance` constraints on the shared engine; a conflicting dimension is localised and the least trusted one is dropped *and reported*) → rooms (flood fill, exact area for orthogonal rooms).

* Result `spadra.reconstruction.v1`: every element has a `confidence` and `evidence`. Confidence is an **evidence score, not a probability** (see limits).
* Corrections (`scale_mm_per_px`, `unit`, `dimensions`, `add_dimensions`, `remove`, `walls`, `openings`, `add_walls`) re-run the whole pipeline deterministically; unknown keys are refused, unmatched ids warn.
* `POST /api/reconstruct/run` is read-only. `reconstruct.draw` writes layers `A-WALL`, `A-OPEN`, `A-ROOM` into a drawing; `reconstruct.build` creates BIM walls, openings and rooms (existing commands, IFC classes, provenance `spadra:recon`, `min_confidence` filter). Each is one Undo step.
* UI: **Model → Reconstruct**: import, Detect, overlay (source, lines, dimensions, walls, openings, rooms toggles), correct, Re-run, Generate (BIM / drawing / both) or Cancel.

## 4. Agents (`agent_hub.py`, `GET /api/agents/roles`)

Agents only propose, analyse or verify. The table of roles and what is real now is served by `/api/agents/roles`. Integrated and used: STEM, Spatial, Optimization. **Not integrated, with observed reasons:** Language, Planning (PyTorch not importable where built), Verification (scope is formal objects; specs are verified by measurement), Provenance (not importable), Software (it analyses source code; did not import here: `ModuleNotFoundError: logs`).

## Reproduce (Windows PowerShell, from `G:\GAIA\AI\SLAI`)

```powershell
py -m pytest applications/SPADRA/tests/test_design_spec.py applications/SPADRA/tests/test_impact.py applications/SPADRA/tests/test_reconstruct.py -q
py -m applications.SPADRA.tests.evaluate_reconstruction --json out_recon.json
py -m applications.SPADRA.tests.evaluate_impact --json out_impact.json
cd applications\SPADRA; tsc -p tsconfig.json --noEmit
```

Measured in the build workspace (no trimesh, shapely, ezdxf, ifcopenshell or torch; Tesseract, OpenCV, pdfplumber present). **Mesh-kernel groups report "unavailable" there; run the impact evaluation where trimesh is installed to measure them.**

| Evaluation | Result |
|---|---|
| Feature tests | 109 passed, 1 skipped |
| Reconstruction, 12 synthetic dimensioned fixtures | dimensions 33/35 found, 33/33 correct; walls 58/58, endpoint error mean 1.67 mm, max 5.92 mm; openings 30/30, offset error mean 5.74 mm, max 11.2 mm; rooms 21/21, max area error 0.295 % |
| Impact, 129 modifier operations | 69 measured by the kernel, all 69 predictions within tolerance (0 outside); 57 unavailable here, 3 refused (predicted empty); 40/40 agree with an independent oracle |

## Limits (evidence-based)

* **Kernel refusals with a valid exact answer.** With trimesh installed, the mesh kernel refused 6 of 30 oblique splits of a box with "degenerate or overflowing mesh triangle" (planes `[2,-1,1]` at origin `[0.6,0,0]` and `[0,1,1]` at `[-0.3,0,0]` / `[0.2,0,0]`), although the exact clip exists. These are counted as `refusals_kernel_limit` in `evaluate_impact`, not hidden and not fixed (the kernel is shared SLAI code).
* Tesseract is a separate program: on Windows install it (`winget install UB-Mannheim.Tesseract-OCR`); SPADRA finds the default install folder or `TESSERACT_CMD`.
* Fixtures are **synthetic, clean** drawings; no real-world scan was evaluated.
* Tesseract returns nothing under heavy grain (σ=25 + blur) or at very low resolution; reconstruction then reports it rather than guessing. 2 of 35 printed dimensions were missed.
* Confidence calibration is weak for walls (Spearman −0.10) and openings (−0.01); it is useful for dimensions (−0.68) and weakly for rooms (−0.25). Do not read it as a probability.
* A door with no leaf drawn is *assumed* (confidence 0.55). Only straight, axis-aligned, dimension-solved walls are supported; curved walls, door swings, hatching and poché are not.
* GOPR-CAD and CAD-WAM could not be verified from the web in the build workspace and are **not reproduced**; only the ReliCAD abstract was verified. The learned-prediction slot is explicitly unavailable.
