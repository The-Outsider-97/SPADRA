/** Pure geometry for picking and dragging parts of assemblies in the 3D view (no DOM, no server): which component a picked face belongs to, and how a
 *  door leaf's drag handle turns about its hinge. Kept apart from the view so it can be tested on its own. */
import type { DoorHandle, Pt3, SceneComponentRun } from "./types.js";

/** The Properties key of the component that face ``face`` belongs to, from the element's face runs (in face order); null outside the runs or for a run
 *  with no key. */
export function partOfFace(runs: readonly SceneComponentRun[], face: number): string | null {
  if (face < 0) return null;
  let start = 0;
  for (const run of runs) {
    if (face < start + run.count) return run.part ?? null;
    start += run.count;
  }
  return null;
}

/** The signed plan angle (radians, counter-clockwise positive) from the closed leaf to ``v``, both measured from the hinge. */
function signedAngle(handle: DoorHandle, v: readonly [number, number]): number {
  const rx = handle.closed[0] - handle.hinge[0], ry = handle.closed[1] - handle.hinge[1];
  return Math.atan2(rx * v[1] - ry * v[0], rx * v[0] + ry * v[1]);
}

/** Which way (+1 counter-clockwise, -1 clockwise, seen from above) a positive opening angle turns this leaf in the world: read off the leaf as the server
 *  drew it when it is shown open, otherwise taken from the hinge's own sign. */
export function leafTurn(handle: DoorHandle): 1 | -1 {
  const drawn = signedAngle(handle, [handle.free[0] - handle.hinge[0], handle.free[1] - handle.hinge[1]]);
  if (handle.open && Math.abs(drawn) > 1e-3) return drawn > 0 ? 1 : -1;
  return handle.sign >= 0 ? 1 : -1;
}

/** Where the leaf's free edge is when it stands ``degrees`` open. */
export function leafPoint(handle: DoorHandle, degrees: number): Pt3 {
  const rx = handle.closed[0] - handle.hinge[0], ry = handle.closed[1] - handle.hinge[1];
  const a = ((degrees * Math.PI) / 180) * leafTurn(handle), c = Math.cos(a), s = Math.sin(a);
  return [handle.hinge[0] + c * rx - s * ry, handle.hinge[1] + s * rx + c * ry, handle.hinge[2]];
}

/** The opening angle (whole degrees, 5–170 as the server accepts) for the pointer at plan point ``p``; ``snap`` rounds to 15° first. */
export function dragAngle(handle: DoorHandle, p: readonly [number, number], snap = false): number {
  let deg = (signedAngle(handle, [p[0] - handle.hinge[0], p[1] - handle.hinge[1]]) * leafTurn(handle) * 180) / Math.PI;
  if (snap) deg = Math.round(deg / 15) * 15;
  return Math.max(5, Math.min(170, Math.round(deg)));
}
