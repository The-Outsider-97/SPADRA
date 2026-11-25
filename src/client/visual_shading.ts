/** How a 3D view is drawn: the five visualization modes, their shared configuration and the shader that draws them.
 *
 *  One module serves the interactive 3D view and the snapshot renderer, so a mode cannot look different in the two: both take the same ``Visual`` (the
 *  per-view setting the server stores, see ``visualization.py``), turn it into the same ``RenderPlan`` and draw with the same fragment shader. The rules
 *  here mirror the server's; ``tests/visual_cases.json`` is a table both languages read, so they cannot drift apart.
 *
 *  What each mode draws:
 *    wireframe  edges only, no faces, no shadows
 *    lined      pale flat faces, strong edges (thickness 1.5x to 3.0x the baseline), shadows optional
 *    realistic  lit and shaded, shadows always, edges off
 *    colored    each face its own colour exactly, shadows optional, baseline edges
 *    gray       the luminance of each colour, shadows optional, baseline edges */
import type { Pt3 } from "./types.js";

export const MODES = ["wireframe", "lined", "realistic", "colored", "gray"] as const;
export type Mode = (typeof MODES)[number];
export const SHADOW_CHOICE = ["lined", "colored", "gray"] as const;
export type ShadowMode = (typeof SHADOW_CHOICE)[number];
export const EDGE_BOOST_RANGE: readonly [number, number] = [50, 200];
export const SUN_ELEVATION_RANGE: readonly [number, number] = [1, 90];

export interface Visual {
  v: 1; mode: Mode; edge_boost: number;
  shadows: Record<ShadowMode, boolean>;
  sun: { azimuth: number; elevation: number };
}

export const DEFAULT_VISUAL: Readonly<Visual> = {
  v: 1, mode: "realistic", edge_boost: 100, shadows: { lined: false, colored: false, gray: false }, sun: { azimuth: 225, elevation: 45 },
};

export class VisualError extends Error { constructor(message: string) { super(message); this.name = "VisualError"; } }

export const MODE_LABEL: Readonly<Record<Mode, string>> = { wireframe: "Wireframe", lined: "Lined", realistic: "Realistic", colored: "Colored", gray: "Gray" };
export const MODE_HINT: Readonly<Record<Mode, string>> = {
  wireframe: "Edges only: nothing is filled and there are no shadows.",
  lined: "Pale faces with strong edges. Shadows are optional.",
  realistic: "Shaded and lit by the sun, with shadows.",
  colored: "Every material in its own plain colour. Shadows are optional.",
  gray: "Plain colours turned to grey by their brightness. Shadows are optional.",
};

export const defaultVisual = (): Visual => ({ ...DEFAULT_VISUAL, shadows: { ...DEFAULT_VISUAL.shadows }, sun: { ...DEFAULT_VISUAL.sun } });
export const edgeFactor = (boost: number): number => 1 + boost / 100;
const isNumber = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const isMode = (x: unknown): x is Mode => typeof x === "string" && (MODES as readonly string[]).includes(x);
const isShadowMode = (x: string): x is ShadowMode => (SHADOW_CHOICE as readonly string[]).includes(x);
const mod360 = (x: number): number => { const r = x % 360; return (r < 0 ? r + 360 : r) + 0; };
const record = (x: unknown): Record<string, unknown> | null => (x && typeof x === "object" && !Array.isArray(x) ? (x as Record<string, unknown>) : null);

/** Whether the mode casts shadows: never in wireframe, always in realistic, as chosen in the other three. */
export function shadowsOn(v: Visual): boolean { return v.mode === "realistic" || (isShadowMode(v.mode) && v.shadows[v.mode]); }

/** A stored value read as a complete, valid setting; each setting that is missing or not allowed falls back to its default, the rest is kept. */
export function normaliseVisual(raw: unknown): Visual {
  const out = defaultVisual();
  const r = record(raw);
  if (!r) return out;
  if (isMode(r["mode"])) out.mode = r["mode"];
  const boost = r["edge_boost"];
  if (isNumber(boost) && boost >= EDGE_BOOST_RANGE[0] && boost <= EDGE_BOOST_RANGE[1]) out.edge_boost = boost;
  const shadows = record(r["shadows"]);
  if (shadows) for (const m of SHADOW_CHOICE) if (typeof shadows[m] === "boolean") out.shadows[m] = shadows[m] as boolean;
  const sun = record(r["sun"]);
  if (sun) {
    if (isNumber(sun["azimuth"])) out.sun.azimuth = mod360(sun["azimuth"]);
    const el = sun["elevation"];
    if (isNumber(el) && el >= SUN_ELEVATION_RANGE[0] && el <= SUN_ELEVATION_RANGE[1]) out.sun.elevation = el;
  }
  return out;
}

export interface VisualPatch { mode?: Mode; edge_boost?: number; shadows?: Partial<Record<ShadowMode, boolean>>; sun?: { azimuth?: number; elevation?: number } }

/** ``current`` with ``patch`` applied. A value that is not allowed is refused with a sentence (not corrected), and nothing changes. */
export function updatedVisual(current: unknown, patch: VisualPatch): Visual {
  const out = normaliseVisual(current);
  const allowed = new Set(["mode", "edge_boost", "shadows", "sun"]);
  for (const k of Object.keys(patch)) if (!allowed.has(k)) throw new VisualError(`'${k}' is not a visualization setting.`);
  if (patch.mode !== undefined) {
    if (!isMode(patch.mode)) throw new VisualError(`The visualization mode must be one of ${MODES.join(", ")}.`);
    out.mode = patch.mode;
  }
  if (patch.edge_boost !== undefined) {
    if (!isNumber(patch.edge_boost)) throw new VisualError("Edge thickness must be a number.");
    if (patch.edge_boost < EDGE_BOOST_RANGE[0] || patch.edge_boost > EDGE_BOOST_RANGE[1]) throw new VisualError(`Edge thickness must be between ${EDGE_BOOST_RANGE[0]} % and ${EDGE_BOOST_RANGE[1]} %.`);
    out.edge_boost = patch.edge_boost;
  }
  if (patch.shadows !== undefined) {
    for (const [m, on] of Object.entries(patch.shadows)) {
      if (!isShadowMode(m)) throw new VisualError(`Shadows can be switched on or off in ${SHADOW_CHOICE.join(", ")} only: wireframe never has them and realistic always does.`);
      if (typeof on !== "boolean") throw new VisualError("Shadows are either on or off.");
      out.shadows[m] = on;
    }
  }
  if (patch.sun !== undefined) {
    const sun = patch.sun;
    if (Object.keys(sun).some((k) => k !== "azimuth" && k !== "elevation")) throw new VisualError("The sun has an azimuth and an elevation.");
    if (sun.azimuth !== undefined) { if (!isNumber(sun.azimuth)) throw new VisualError("Sun azimuth must be a number."); out.sun.azimuth = mod360(sun.azimuth); }
    if (sun.elevation !== undefined) {
      if (!isNumber(sun.elevation)) throw new VisualError("Sun elevation must be a number.");
      if (sun.elevation < SUN_ELEVATION_RANGE[0] || sun.elevation > SUN_ELEVATION_RANGE[1]) throw new VisualError(`Sun elevation must be between ${SUN_ELEVATION_RANGE[0]}° and ${SUN_ELEVATION_RANGE[1]}°.`);
      out.sun.elevation = sun.elevation;
    }
  }
  return out;
}

/** The unit vector towards the sun (x east, y north, z up) for an azimuth clockwise from north and an elevation above the horizon, in degrees. */
export function sunDirection(azimuth: number, elevation: number): Pt3 {
  const az = (azimuth * Math.PI) / 180, el = (elevation * Math.PI) / 180;
  return [Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el)];
}

/** Which controls matter in the current mode: what the 3D view's panel and the snapshot dialog show. Everything else is hidden, never merely disabled. */
export interface Controls { mode: true; edgeThickness: boolean; shadowChoice: boolean; sun: boolean }
export function controlsFor(v: Visual): Controls {
  return { mode: true, edgeThickness: v.mode === "lined", shadowChoice: v.mode === "lined" || v.mode === "colored" || v.mode === "gray", sun: shadowsOn(v) };
}

export type FaceStyle = "none" | "stylised" | "lit" | "solid" | "gray";
/** What drawing a ``Visual`` means, in the terms both renderers use. */
export interface RenderPlan {
  faces: FaceStyle;
  /** Edge lines (null: none) and how thick, as a multiple of the baseline. */
  edges: { factor: number } | null;
  shadows: boolean;
  sun: Pt3;
  /** The shader's mode number (see ``MESH_FS``). */
  shaderMode: 0 | 1 | 2 | 3;
  /** Ambient occlusion and tone mapping are enhancements of Realistic only: the other modes draw the plain colours they promise. */
  enhancements: boolean;
}

export function renderPlan(v: Visual): RenderPlan {
  const sun = sunDirection(v.sun.azimuth, v.sun.elevation);
  const shadows = shadowsOn(v);
  switch (v.mode) {
    case "wireframe": return { faces: "none", edges: { factor: 1 }, shadows: false, sun, shaderMode: 1, enhancements: false };
    case "lined": return { faces: "stylised", edges: { factor: edgeFactor(v.edge_boost) }, shadows, sun, shaderMode: 3, enhancements: false };
    case "realistic": return { faces: "lit", edges: null, shadows: true, sun, shaderMode: 0, enhancements: true };
    case "colored": return { faces: "solid", edges: { factor: 1 }, shadows, sun, shaderMode: 1, enhancements: false };
    case "gray": return { faces: "gray", edges: { factor: 1 }, shadows, sun, shaderMode: 2, enhancements: false };
  }
}

/** The baseline edge line weight in CSS pixels of the view. A snapshot scales it by (picture height / view height) so the lines keep their proportion. */
export const BASE_EDGE_PX = 1.25;
export function edgePixels(plan: RenderPlan, scale = 1): number { return plan.edges ? BASE_EDGE_PX * plan.edges.factor * scale : 0; }

/** The orthographic box a shadow map covers: around the model's bounding sphere, looking along the sun. Pure, so both renderers fit it identically. */
export interface LightFit { eye: Pt3; target: Pt3; up: Pt3; half: number; near: number; far: number }
export function fitLight(bounds: readonly [Pt3, Pt3] | null, sun: Pt3): LightFit {
  const [lo, hi] = bounds ?? [[-5, -5, 0], [5, 5, 3]];
  const target: Pt3 = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 0.5);
  return { eye: [target[0] + sun[0] * radius * 2.2, target[1] + sun[1] * radius * 2.2, target[2] + sun[2] * radius * 2.2], target,
    up: Math.abs(sun[2]) > 0.98 ? [0, 1, 0] : [0, 0, 1], half: radius * 1.05, near: 0.01, far: radius * 4.4 };
}

// ------------------------------------------------------------------------------------------------------------------------------ shaders
/** Shadow darkening of the flat modes: how much of its colour a face in shadow keeps. */
export const FLAT_SHADOW_KEEP = 0.55;

export const MESH_VS = `#version 300 es
in vec3 a_pos; in vec3 a_normal; uniform mat4 u_vp; uniform mat4 u_light;
out vec3 v_normal; out vec3 v_pos; out vec4 v_shadow;
void main() { v_normal = a_normal; v_pos = a_pos; v_shadow = u_light * vec4(a_pos, 1.0); gl_Position = u_vp * vec4(a_pos, 1.0); }`;

/** u_mode: 0 realistic (lit), 1 colored (the colour as it is), 2 gray (its luminance), 3 lined (pale flat faces). u_encode: 1 writes display (sRGB-ish)
 *  values, as the interactive view needs; 0 writes linear values for a post pass that encodes, as the snapshot does. Both come to the same picture. */
export const MESH_FS = `#version 300 es
precision highp float; precision highp sampler2DShadow;
in vec3 v_normal; in vec3 v_pos; in vec4 v_shadow;
uniform vec3 u_color; uniform vec3 u_sun; uniform vec3 u_eye; uniform vec3 u_viewDir; uniform float u_ortho; uniform float u_alpha;
uniform float u_mode; uniform float u_encode; uniform float u_flatKeep;
uniform float u_shadowOn; uniform float u_shadowTexel; uniform sampler2DShadow u_shadowMap;
uniform float u_gradient; uniform vec3 u_top; uniform vec3 u_bottom; uniform float u_zMin; uniform float u_zMax;
uniform float u_boundary; uniform float u_fadeWidth; uniform float u_abrupt;
uniform vec3 u_tint; uniform float u_tintAmount;
out vec4 o;
vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }
float shadowAt(vec3 n, vec3 l) {
  if (u_shadowOn < 0.5) return 1.0;
  float slope = 1.0 - clamp(dot(n, l), 0.0, 1.0);
  vec3 p = v_shadow.xyz / v_shadow.w * 0.5 + 0.5;
  if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) return 1.0;
  p.z -= 0.0008 + 0.004 * slope;
  float s = 0.0;
  for (int i = -1; i <= 1; i++) for (int j = -1; j <= 1; j++) s += texture(u_shadowMap, vec3(p.xy + vec2(float(i), float(j)) * u_shadowTexel, p.z));
  return s / 9.0;
}
void main() {
  vec3 n = normalize(v_normal);
  vec3 toEye = u_ortho > 0.5 ? -u_viewDir : normalize(u_eye - v_pos);
  if (dot(n, toEye) < 0.0) n = -n;                                  // a surface is lit on the side that is seen (walls and slabs are drawn two-sided)
  vec3 base = u_color;
  if (u_gradient > 0.5) {
    float t = clamp((v_pos.z - u_zMin) / max(u_zMax - u_zMin, 0.000001), 0.0, 1.0);
    float blend = (u_abrupt > 0.5 || u_fadeWidth < 0.00001) ? step(u_boundary, t) : smoothstep(u_boundary - u_fadeWidth * 0.5, u_boundary + u_fadeWidth * 0.5, t);
    base = mix(u_bottom, u_top, blend);
  }
  float lit = shadowAt(n, u_sun);                                   // 1 in the light, 0 in shadow (1 when shadows are off)
  vec3 col;
  if (u_mode < 0.5) {
    float diffuse = max(dot(n, u_sun), 0.0);
    float sky = 0.5 + 0.5 * n.z;
    vec3 ambient = mix(vec3(0.30, 0.29, 0.28), vec3(0.50, 0.55, 0.62), sky);
    vec3 direct = vec3(1.0, 0.96, 0.88) * 1.55 * diffuse * lit;
    col = lin(base) * (ambient + direct);
  } else {
    vec3 a = lin(base);
    if (u_mode > 1.5 && u_mode < 2.5) a = vec3(dot(a, vec3(0.2126, 0.7152, 0.0722)));              // gray: the luminance of the colour
    if (u_mode > 2.5) a = mix(vec3(1.0), a, 0.22) * (0.93 + 0.07 * max(dot(n, u_sun), 0.0));     // lined: pale faces, so that the edges carry the drawing
    col = a * mix(u_flatKeep, 1.0, lit);                            // a face in shadow keeps part of its colour; a face in the light keeps all of it
  }
  vec3 shown = u_encode > 0.5 ? pow(col, vec3(1.0 / 2.2)) : col;
  shown = mix(shown, u_tint, u_tintAmount);                         // the view's selection and relationship tint; always 0 in a snapshot
  o = vec4(shown, u_alpha);
}`;

export const DEPTH_VS = `#version 300 es
in vec3 a_pos; uniform mat4 u_vp; void main() { gl_Position = u_vp * vec4(a_pos, 1.0); }`;
export const DEPTH_FS = `#version 300 es
precision highp float; out vec4 o; void main() { o = vec4(1.0); }`;

/** Edges as screen-space quads: WebGL draws every line one pixel wide, so a thicker edge is a rectangle along the segment, ``u_width`` pixels across.
 *  One instance per segment (two vec3 endpoints). A segment that crosses the near plane is cut there; one wholly behind the eye is dropped. */
export const EDGE_VS = `#version 300 es
in vec3 a_a; in vec3 a_b; uniform mat4 u_vp; uniform vec2 u_viewport; uniform float u_width; uniform float u_depthBias;
void main() {
  vec4 ca = u_vp * vec4(a_a, 1.0), cb = u_vp * vec4(a_b, 1.0);
  const float EPS = 0.0001;
  if (ca.w < EPS && cb.w < EPS) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
  if (ca.w < EPS) ca = mix(ca, cb, (EPS - ca.w) / (cb.w - ca.w));
  if (cb.w < EPS) cb = mix(cb, ca, (EPS - cb.w) / (ca.w - cb.w));
  vec2 sa = ca.xy / ca.w * 0.5 * u_viewport, sb = cb.xy / cb.w * 0.5 * u_viewport;
  vec2 d = sb - sa; float len = length(d);
  d = len > 1e-5 ? d / len : vec2(1.0, 0.0);
  vec2 nrm = vec2(-d.y, d.x);
  float end = float(gl_VertexID >> 1), side = float(gl_VertexID & 1) * 2.0 - 1.0;
  vec4 c = mix(ca, cb, end);
  vec2 s = (end > 0.5 ? sb : sa) + d * (end * 2.0 - 1.0) * u_width * 0.5 + nrm * side * u_width * 0.5;
  gl_Position = vec4(s / (0.5 * u_viewport) * c.w, c.z - u_depthBias * c.w, c.w);
}`;
export const EDGE_FS = `#version 300 es
precision highp float; uniform vec4 u_color; out vec4 o; void main() { o = u_color; }`;
