# Relationships, measurements, property transfer and 3D pictures

Where things are: `relationships.py` (links, groups, attachments, propagation), `measurements.py`, `property_transfer.py`, the client modules
`relationships_ui.ts`, `measure_ui.ts`, `transfer_ui.ts`, `snapshot_render.ts`, `snapshot_ui.ts`, and the routes `/api/relationships`, `/api/measure*`,
`/api/properties/transfer-info`. Everything is stored in the project manifest (`relationships`, `measurements`) and only appears when used, so existing projects
open unchanged. Every change goes through the normal command system, so it is one undo step and is validated before it is applied.

## Tests

* `tests/test_relationships.py` (45), `tests/test_property_transfer.py` (19), `tests/test_measurements.py` (33), `tests/test_mirroring.py` (21),
  `tests/test_transfer_modifiers_connections.py` (16, one needs `trimesh`).
* `tests_ts/snapshot.test.ts` (sizes, camera fit, mesh arrays, file names). The whole TypeScript suite and `tsc` pass.

## Mirror Element (`mirroring.py`, command `model.mirror`)

A vertical plane through a plan line. Each element is mirrored by its own meaning, never by negating numbers: a wall is reflected and its ends swapped so its left
and right (exterior) sides keep their meaning; its doors and windows keep their place along it (offset = length - offset), their hinge flips and their swing side stays;
roofs are rebuilt and **verified by mesh reflection** before they are accepted; columns, beams, grids, floors, rooms, extrusions and sweeps follow. A shape whose mirror
image is a different shape (stairs, handed profiles) is refused. Each element is done on its own: one that fails is put back exactly and reported, the others are done.
Up to 300 elements per request. Copies join the groups and links of their originals (`model.array` with `persistent: false` does the same for Paste Element).

## Property transfer: modifiers, connections, reference plane

* **Modifiers**: chamfer and bend are copied (the target's own chamfer and bend are replaced). A bend's angle changes sign when the faces are swapped. Boolean, split,
  face edits, weld and subdivision point at other elements or at faces of the source and are named as not transferred. Only generic objects, extrusions, sweeps and family
  instances take modifiers; the others say so. The modifier is evaluated on the target and a target it does not fit is reported failed and put back.
* **Connections**: a wall or curtain wall takes the same roof, level (top) or floor (base) and offset; a gable or shed roof takes the end connections eave for eave (swapped
  eave when mirrored); the wall a connection was resolved against is found again from the walls under each eave. Wall joins are computed from the walls that meet and are not copied.
* **Reference plane** (Mirror Properties): two plan points. Parallel to the element's length = its sides, square to it = its ends; within 1 degree. Any other angle, and
  an element with no direction (a floor), is refused with the reason and nothing is changed.

## Plan view

Measure works in plan views (distance, length, angle, area and perimeter of an outline, radius, coordinates; Keep works as in 3D). Links and groups are tinted on the
plan and parent-to-child arrows are drawn, as in 3D. Mirror with elements selected asks for the two points of the plane and then for Keep / Replace originals.

## What is not done, and what has not been checked

* **Browser checks done** (Chrome with software GL, Playwright, in the cloud copy of the app): Link dialog and propagation on the plan, tint and arrows in 3D and on the
  plan, Group / Ungroup (selecting one member selects the group, moving moves both), Attach / Free the Top, the Relationships panel, Copy / Paste Element (links kept),
  Mirror Element from the menu, the ribbon and the plan tool, Mirror Properties with a reference plane, Measure in 3D and on the plan with Keep, the Snapshot dialog and a
  1920 x 1312 PNG with shadows and ambient occlusion. **Not checked**: a real GPU (all rendering was software), Windows, touch input.
* **Bend and chamfer were not run through the real mesh kernel**: `trimesh` is not installed here, so the transfer is tested against a stand-in for the evaluation (the
  copy, the replacement, the sign change and the roll-back are real; whether a given bend fits a given target is decided by the kernel). One test exercises the real kernel
  and is skipped until `trimesh` is present.
* Scaling does not propagate through links (move and rotate do); there is no element scale command. Derived elements (doors, windows and the like that follow a host) cannot
  be linked; they move with their host.
* A bend's span is measured from the element's own origin, so Mirror Properties along the length keeps it and says so. The `length` dimension is excluded from transfer.
* Mirror Element refuses elements whose mirror image is a different shape (stairs, handed profiles, handed families) instead of guessing.
* Measure's default key is U (M is Mirror).
* The shipped `dist/` was rebuilt from these sources. The two `test_startup` failures that remain (an old `colorama`, and `PyInstaller` not being in
  `requirements.txt`) come from the environment, not from these changes.
