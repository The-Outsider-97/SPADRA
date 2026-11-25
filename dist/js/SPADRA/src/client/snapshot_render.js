/** Offscreen 3D snapshots.
 *
 *  A snapshot is drawn by a renderer of its own: a detached canvas with its own WebGL 2 context, sized to the picture asked for and not to the view on
 *  screen. It reads what the view hands it (the meshes, the camera, the colours) and gives back a picture; it never touches the view's canvas, its
 *  camera, its selection or the project, so taking a snapshot changes nothing.
 *
 *  Per picture: a shadow map from the sun, the scene into a multisampled framebuffer, a resolve, then one post pass that adds ambient occlusion from the
 *  depth, tone-maps, composes over the chosen background and writes the pixels. Between the stages the work yields to the browser, reports progress and
 *  checks for cancel. A size or feature the GPU cannot give is refused with the reason (never silently drawn smaller). */
import { cameraEye, lookAt, multiply, ortho, perspective } from "./math3d.js";
import { attribute as attr, compileProgram, edgeOffsetFactor, EdgeRenderer, GlUnavailable, placeholderShadowTexture, ShadowPass, uniform as uni } from "./visual_gl.js";
import { DEFAULT_VISUAL, edgePixels, FLAT_SHADOW_KEEP, MESH_FS, MESH_VS, renderPlan } from "./visual_shading.js";
export const RESOLUTIONS = [
    { id: "720p", label: "HD 720p (1280 px)", edge: 1280 }, { id: "1080p", label: "Full HD 1080p (1920 px)", edge: 1920 },
    { id: "1440p", label: "QHD 1440p (2560 px)", edge: 2560 }, { id: "4k", label: "4K UHD (3840 px)", edge: 3840 },
];
export const ASPECTS = [
    { id: "view", label: "Same as the view", ratio: null }, { id: "16:9", label: "16:9", ratio: 16 / 9 }, { id: "3:2", label: "3:2", ratio: 3 / 2 },
    { id: "4:3", label: "4:3", ratio: 4 / 3 }, { id: "1:1", label: "Square", ratio: 1 }, { id: "9:16", label: "9:16 portrait", ratio: 9 / 16 },
];
export const MAX_PIXELS = 64_000_000;
export class SnapshotCancelled extends Error {
    constructor() { super("The snapshot was cancelled."); this.name = "SnapshotCancelled"; }
}
export class SnapshotUnavailable extends GlUnavailable {
    constructor(message) { super(message); this.name = "SnapshotUnavailable"; }
}
/** The picture size for a long edge and an aspect ratio (width over height). The long edge is the larger side. */
export function sizeFor(longEdge, ratio) {
    const r = ratio > 0 && Number.isFinite(ratio) ? ratio : 16 / 9;
    return r >= 1 ? { width: Math.round(longEdge), height: Math.max(1, Math.round(longEdge / r)) } : { width: Math.max(1, Math.round(longEdge * r)), height: Math.round(longEdge) };
}
/** What each quality turns on. The person may still switch the four options on or off afterwards. */
export function qualityDefaults(q) {
    return q === "draft" ? { antialias: true, occlusion: false, toneMapping: true } : { antialias: true, occlusion: true, toneMapping: true };
}
const SHADOW_SIZE = { draft: 1024, standard: 2048, high: 4096, maximum: 8192 };
const AO_TAPS = { draft: 8, standard: 12, high: 20, maximum: 32 };
const MSAA = { draft: 4, standard: 4, high: 8, maximum: 16 };
export function defaultSettings(width, height) {
    return { width, height, quality: "standard", ...qualityDefaults("standard"), projection: "view", framing: "view", background: { kind: "paper", colour: "#ffffff" },
        overlays: { edges: false, grid: false, axes: false }, format: "png", jpegQuality: 0.92 };
}
/** The camera a snapshot is taken with: the view's own, unless the settings ask for the other projection or for the whole model in frame. Never changes the one it is given. */
export function snapshotCamera(input, s) {
    const cam = { ...input.camera, target: [...input.camera.target] };
    if (s.projection !== "view")
        cam.ortho = s.projection === "orthographic";
    if (s.framing === "fit" && input.bounds) {
        const [lo, hi] = input.bounds;
        cam.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
        const radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 0.5) * 1.08;
        const aspect = s.width / Math.max(1, s.height);
        const half = Math.PI / 8;
        cam.distance = cam.ortho ? radius / Math.min(1, aspect) / Math.tan(half) : radius / Math.sin(Math.min(half, Math.atan(Math.tan(half) * aspect)));
    }
    return cam;
}
/** Checks a request against what this GPU can do and says exactly what is wrong. Returns the number of samples the multisampling will really use. */
export function checkLimits(gl, s) {
    if (!Number.isInteger(s.width) || !Number.isInteger(s.height) || s.width < 16 || s.height < 16)
        throw new SnapshotUnavailable("The picture must be at least 16 × 16 pixels.");
    if (s.width * s.height > MAX_PIXELS)
        throw new SnapshotUnavailable(`That is ${(s.width * s.height / 1e6).toFixed(1)} million pixels; the most a snapshot can be is ${MAX_PIXELS / 1e6} million.`);
    const side = Math.min(gl.getParameter(gl.MAX_TEXTURE_SIZE), gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), gl.getParameter(gl.MAX_VIEWPORT_DIMS)[0] ?? 4096);
    if (s.width > side || s.height > side)
        throw new SnapshotUnavailable(`This graphics card draws pictures up to ${side} pixels on a side; ${s.width} × ${s.height} is too big. Choose a smaller size.`);
    if (!s.antialias)
        return 0;
    return Math.min(MSAA[s.quality], gl.getParameter(gl.MAX_SAMPLES));
}
// ------------------------------------------------------------------------------------------------------------------------------ shaders
// The mesh and shadow shaders are the interactive view's own (visual_shading.ts), so that a mode cannot look different in a picture.
const LINE_VS = `#version 300 es
in vec3 a_pos; uniform mat4 u_vp; void main() { gl_Position = u_vp * vec4(a_pos, 1.0); }`;
const LINE_FS = `#version 300 es
precision highp float; uniform vec4 u_color; out vec4 o; void main() { o = u_color; }`;
const POST_VS = `#version 300 es
out vec2 v_uv; void main() { vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); v_uv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }`;
const POST_FS = `#version 300 es
precision highp float;
in vec2 v_uv; out vec4 o;
uniform sampler2D u_color; uniform sampler2D u_depth; uniform vec2 u_size;
uniform float u_ao; uniform float u_aoTaps; uniform float u_aoRadiusPx; uniform float u_aoRange; uniform float u_aoStrength;
uniform float u_near; uniform float u_far; uniform float u_ortho;
uniform float u_tone; uniform vec4 u_background; uniform float u_transparent;
float linearDepth(float d) {
  if (u_ortho > 0.5) return mix(u_near, u_far, d);
  float z = d * 2.0 - 1.0;
  return 2.0 * u_near * u_far / (u_far + u_near - z * (u_far - u_near));
}
float noise(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
void main() {
  vec4 c = texture(u_color, v_uv);
  float depth = texture(u_depth, v_uv).r;
  float a = c.a;
  vec3 rgb = a > 0.0001 ? c.rgb / a : vec3(0.0);                         // the resolve averages premultiplied by the clear (transparent), so undo it
  if (u_ao > 0.5 && depth < 0.99999) {
    float zc = linearDepth(depth);
    float occ = 0.0;
    float spin = noise(gl_FragCoord.xy) * 6.2831853;
    for (int i = 0; i < 64; i++) {
      if (float(i) >= u_aoTaps) break;
      float k = (float(i) + 0.5) / u_aoTaps;
      float ang = spin + float(i) * 2.399963;
      vec2 off = vec2(cos(ang), sin(ang)) * (k * k * 0.85 + 0.15) * u_aoRadiusPx / u_size;
      vec2 uv = v_uv + off;
      if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) continue;
      float ds = texture(u_depth, uv).r;
      if (ds >= 0.99999) continue;
      float dz = zc - linearDepth(ds);                                  // positive: the neighbour is nearer to the camera, so it blocks light
      float bias = u_aoRange * 0.02;
      if (dz > bias) occ += clamp(1.0 - (dz - bias) / u_aoRange, 0.0, 1.0);
    }
    rgb *= 1.0 - u_aoStrength * clamp(occ / u_aoTaps * 1.6, 0.0, 1.0);
  }
  vec3 col = u_tone > 0.5 ? aces(rgb * 0.95) : clamp(rgb, 0.0, 1.0);
  col = pow(col, vec3(1.0 / 2.2));
  if (u_transparent > 0.5) { o = vec4(col, a); return; }
  o = vec4(mix(u_background.rgb, col, a), 1.0);
}`;
// ------------------------------------------------------------------------------------------------------------------------------ helpers
const compile = (gl, vs, fs) => {
    try {
        return compileProgram(gl, vs, fs, "The snapshot");
    }
    catch (error) {
        if (error instanceof GlUnavailable)
            throw new SnapshotUnavailable(error.message);
        throw error;
    }
};
const nextFrame = () => new Promise((resolve) => { requestAnimationFrame(() => resolve()); });
function parseHex(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m || !m[1])
        return [1, 1, 1];
    const n = Number.parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
/** Draws the snapshot and returns the encoded picture. Rejects with ``SnapshotCancelled`` when ``signal`` aborts and with ``SnapshotUnavailable`` (and a reason) when it cannot. */
export async function renderSnapshot(input, s, hooks = {}) {
    const step = async (fraction, label) => {
        hooks.onProgress?.(fraction, label);
        await nextFrame();
        if (hooks.signal?.aborted)
            throw new SnapshotCancelled();
    };
    const canvas = document.createElement("canvas");
    canvas.width = s.width;
    canvas.height = s.height;
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: true, premultipliedAlpha: false, preserveDrawingBuffer: true, depth: false, stencil: false });
    if (!gl)
        throw new SnapshotUnavailable("This browser does not provide WebGL 2, which snapshots need.");
    const gpu = { buffers: [], textures: [], fbos: [], rbos: [], vao: [], programs: [] };
    let shadowPass = null, edgeRenderer = null;
    const release = () => {
        for (const b of gpu.buffers)
            gl.deleteBuffer(b);
        for (const t of gpu.textures)
            gl.deleteTexture(t);
        for (const f of gpu.fbos)
            gl.deleteFramebuffer(f);
        for (const r of gpu.rbos)
            gl.deleteRenderbuffer(r);
        for (const v of gpu.vao)
            gl.deleteVertexArray(v);
        for (const p of gpu.programs)
            gl.deleteProgram(p);
        shadowPass?.dispose();
        edgeRenderer?.dispose();
        gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
    try {
        const samples = checkLimits(gl, s);
        const floatTarget = gl.getExtension("EXT_color_buffer_float") !== null;
        await step(0.03, "Preparing the renderer");
        const visual = s.visual ?? input.visual ?? DEFAULT_VISUAL; // a mode picked in the dialog is for this picture only; the view keeps its own
        const plan = renderPlan(visual);
        const cam = snapshotCamera(input, s);
        const aspect = s.width / s.height;
        const [lo, hi] = input.bounds ?? [[-5, -5, 0], [5, 5, 3]];
        const radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 0.5);
        const far = Math.max(cam.distance + input.extent * 4, 10);
        const near = Math.max(cam.distance * 0.01, 0.01);
        const view = lookAt(cameraEye(cam), cam.target, [0, 0, 1]);
        const halfH = cam.distance * Math.tan(Math.PI / 8);
        const projection = cam.ortho ? ortho(halfH * aspect, halfH, -far, far) : perspective(Math.PI / 4, aspect, near, far);
        const vp = multiply(projection, view);
        const eye = cameraEye(cam);
        const viewDir = (() => { const d = [cam.target[0] - eye[0], cam.target[1] - eye[1], cam.target[2] - eye[2]]; const l = Math.hypot(...d) || 1; return [d[0] / l, d[1] / l, d[2] / l]; })();
        // geometry: one buffer a mesh, uploaded once, shared by the shadow pass and the colour pass
        const meshes = [];
        for (const item of input.items) {
            if (item.construction || item.positions.length === 0)
                continue; // construction cutters are for editing, never for a picture
            const tri = gl.createBuffer(), edge = item.edges.length ? gl.createBuffer() : null;
            if (!tri)
                throw new SnapshotUnavailable("This browser ran out of graphics memory.");
            gpu.buffers.push(tri);
            if (edge)
                gpu.buffers.push(edge);
            gl.bindBuffer(gl.ARRAY_BUFFER, tri);
            gl.bufferData(gl.ARRAY_BUFFER, item.positions, gl.STATIC_DRAW);
            if (edge) {
                gl.bindBuffer(gl.ARRAY_BUFFER, edge);
                gl.bufferData(gl.ARRAY_BUFFER, item.edges, gl.STATIC_DRAW);
            }
            meshes.push({ item, tri, edge, triCount: item.positions.length / 6, edgeCount: item.edges.length / 3 });
        }
        const lineBuffer = (data) => {
            if (!data || !data.length)
                return null;
            const buf = gl.createBuffer();
            if (!buf)
                return null;
            gpu.buffers.push(buf);
            gl.bindBuffer(gl.ARRAY_BUFFER, buf);
            gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
            return { buf, count: data.length / 3 };
        };
        const grid = s.overlays.grid ? lineBuffer(input.grid) : null;
        const axes = s.overlays.axes ? lineBuffer(input.axes) : null;
        await step(0.12, "Sending the model to the graphics card");
        const meshProg = compile(gl, MESH_VS, MESH_FS), lineProg = compile(gl, LINE_VS, LINE_FS), postProg = compile(gl, POST_VS, POST_FS);
        gpu.programs.push(meshProg.prog, lineProg.prog, postProg.prog);
        edgeRenderer = new EdgeRenderer(gl);
        const vao = gl.createVertexArray();
        if (!vao)
            throw new SnapshotUnavailable("This browser could not create a vertex array.");
        gpu.vao.push(vao);
        gl.bindVertexArray(vao);
        // ---- the sun's shadow map, when the mode casts shadows: the same sun and the same fitting the view uses
        const shadowSize = Math.min(SHADOW_SIZE[s.quality], gl.getParameter(gl.MAX_TEXTURE_SIZE));
        let shadowTex = null, lightVp = new Float32Array(16);
        if (plan.shadows && plan.faces !== "none" && meshes.length > 0) {
            await step(0.2, "Casting shadows");
            try {
                shadowPass = new ShadowPass(gl, shadowSize);
            }
            catch (error) {
                if (error instanceof GlUnavailable)
                    throw new SnapshotUnavailable(error.message);
                throw error;
            }
            const casters = meshes.map((m) => ({ buf: m.tri, count: m.triCount }));
            gl.bindVertexArray(vao);
            shadowPass.render(input.bounds, plan.sun, casters, "snapshot");
            shadowTex = shadowPass.texture;
            lightVp = shadowPass.lightVp;
        }
        await step(0.35, "Drawing the model");
        const placeholder = shadowTex ? null : placeholderShadowTexture(gl);
        if (placeholder)
            gpu.textures.push(placeholder);
        // ---- the colour target: multisampled, with a resolved copy of the colour and the depth
        const colourFormat = floatTarget ? gl.RGBA16F : gl.RGBA8;
        const mk = (value, what) => { if (!value)
            throw new SnapshotUnavailable(`This browser ran out of graphics memory (${what}).`); return value; };
        const msColour = mk(gl.createRenderbuffer(), "colour"), msDepth = mk(gl.createRenderbuffer(), "depth"), msFbo = mk(gl.createFramebuffer(), "frame");
        gpu.rbos.push(msColour, msDepth);
        gpu.fbos.push(msFbo);
        gl.bindRenderbuffer(gl.RENDERBUFFER, msColour);
        gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, colourFormat, s.width, s.height);
        gl.bindRenderbuffer(gl.RENDERBUFFER, msDepth);
        gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.DEPTH_COMPONENT24, s.width, s.height);
        gl.bindFramebuffer(gl.FRAMEBUFFER, msFbo);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, msColour);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, msDepth);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
            throw new SnapshotUnavailable("This graphics card cannot draw a picture of this size with these options; try a smaller size or switch anti-aliasing off.");
        const colourTex = mk(gl.createTexture(), "colour copy"), depthTex = mk(gl.createTexture(), "depth copy"), resolveFbo = mk(gl.createFramebuffer(), "resolve");
        gpu.textures.push(colourTex, depthTex);
        gpu.fbos.push(resolveFbo);
        gl.bindTexture(gl.TEXTURE_2D, colourTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, colourFormat, s.width, s.height, 0, gl.RGBA, floatTarget ? gl.HALF_FLOAT : gl.UNSIGNED_BYTE, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.bindTexture(gl.TEXTURE_2D, depthTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, s.width, s.height, 0, gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.bindFramebuffer(gl.FRAMEBUFFER, resolveFbo);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, colourTex, 0);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, depthTex, 0);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
            throw new SnapshotUnavailable("This graphics card cannot keep a copy of the picture to finish it; try a smaller size.");
        gl.bindFramebuffer(gl.FRAMEBUFFER, msFbo);
        gl.viewport(0, 0, s.width, s.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clearDepth(1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.enable(gl.DEPTH_TEST);
        gl.depthFunc(gl.LEQUAL);
        gl.depthMask(true);
        gl.disable(gl.BLEND);
        const scale = s.height / Math.max(1, input.viewHeight); // edges at the weight the mode asks for, in proportion to the picture exactly as in the view
        const edgePx = plan.edges ? edgePixels(plan, scale) : s.overlays.edges ? edgePixels({ ...plan, edges: { factor: 1 } }, scale) : 0;
        gl.enable(gl.POLYGON_OFFSET_FILL);
        gl.polygonOffset(edgeOffsetFactor(edgePx), 1); // so the edges drawn on the faces are not eaten by them, however thick they are
        gl.useProgram(meshProg.prog);
        const set1 = (p, n, v) => gl.uniform1f(uni(gl, p, n), v);
        const set3 = (p, n, v) => gl.uniform3f(uni(gl, p, n), v[0] ?? 0, v[1] ?? 0, v[2] ?? 0);
        gl.uniformMatrix4fv(uni(gl, meshProg, "u_vp"), false, vp);
        gl.uniformMatrix4fv(uni(gl, meshProg, "u_light"), false, lightVp);
        set3(meshProg, "u_sun", plan.sun);
        set3(meshProg, "u_eye", eye);
        set3(meshProg, "u_viewDir", viewDir);
        set1(meshProg, "u_ortho", cam.ortho ? 1 : 0);
        set1(meshProg, "u_alpha", 1);
        set1(meshProg, "u_mode", plan.shaderMode);
        set1(meshProg, "u_encode", 0);
        set1(meshProg, "u_flatKeep", FLAT_SHADOW_KEEP);
        set1(meshProg, "u_tintAmount", 0);
        set1(meshProg, "u_shadowOn", shadowTex ? 1 : 0);
        set1(meshProg, "u_shadowTexel", 1 / (shadowPass?.size ?? 1));
        gl.uniform1i(uni(gl, meshProg, "u_shadowMap"), 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, shadowTex ?? placeholder);
        const mPos = attr(gl, meshProg, "a_pos"), mNor = attr(gl, meshProg, "a_normal");
        gl.enableVertexAttribArray(mPos);
        gl.enableVertexAttribArray(mNor);
        if (plan.faces !== "none")
            for (const m of meshes) { // wireframe fills nothing
                gl.bindBuffer(gl.ARRAY_BUFFER, m.tri);
                gl.vertexAttribPointer(mPos, 3, gl.FLOAT, false, 24, 0);
                gl.vertexAttribPointer(mNor, 3, gl.FLOAT, false, 24, 12);
                let first = 0;
                for (const part of m.item.parts) {
                    set1(meshProg, "u_gradient", part.gradient ? 1 : 0);
                    if (part.gradient) {
                        set3(meshProg, "u_top", part.gradient.top);
                        set3(meshProg, "u_bottom", part.gradient.bottom);
                        set1(meshProg, "u_zMin", m.item.zMin);
                        set1(meshProg, "u_zMax", m.item.zMax);
                        set1(meshProg, "u_boundary", part.gradient.position);
                        set1(meshProg, "u_fadeWidth", part.gradient.fade);
                        set1(meshProg, "u_abrupt", part.gradient.abrupt ? 1 : 0);
                    }
                    set3(meshProg, "u_color", part.color);
                    gl.drawArrays(gl.TRIANGLES, first, part.count);
                    first += part.count;
                }
            }
        gl.disableVertexAttribArray(mPos);
        gl.disableVertexAttribArray(mNor);
        await step(0.55, "Drawing the edges");
        gl.disable(gl.POLYGON_OFFSET_FILL);
        gl.useProgram(lineProg.prog);
        gl.uniformMatrix4fv(uni(gl, lineProg, "u_vp"), false, vp);
        const lPos = attr(gl, lineProg, "a_pos");
        gl.enableVertexAttribArray(lPos);
        const colourOf = (c, a = 1) => gl.uniform4f(uni(gl, lineProg, "u_color"), Math.pow(c[0] ?? 0, 2.2), Math.pow(c[1] ?? 0, 2.2), Math.pow(c[2] ?? 0, 2.2), a);
        if (grid) {
            gl.bindBuffer(gl.ARRAY_BUFFER, grid.buf);
            gl.vertexAttribPointer(lPos, 3, gl.FLOAT, false, 0, 0);
            colourOf(input.colours.grid);
            gl.drawArrays(gl.LINES, 0, grid.count);
        }
        if (axes) {
            gl.bindBuffer(gl.ARRAY_BUFFER, axes.buf);
            gl.vertexAttribPointer(lPos, 3, gl.FLOAT, false, 0, 0);
            colourOf(input.colours.axisX);
            gl.drawArrays(gl.LINES, 0, 2);
            colourOf(input.colours.axisY);
            gl.drawArrays(gl.LINES, 2, 2);
        }
        gl.disableVertexAttribArray(lPos);
        // Realistic has no edges unless the person adds them
        const ink = input.colours.ink;
        if (edgePx > 0) {
            const colour = [Math.pow(ink[0], 2.2), Math.pow(ink[1], 2.2), Math.pow(ink[2], 2.2), 1];
            for (const m of meshes)
                if (m.edge)
                    edgeRenderer.draw(m.edge, m.edgeCount / 2, vp, [s.width, s.height], edgePx, colour);
        }
        await step(0.7, "Smoothing edges");
        // ---- resolve the multisampled picture into plain textures
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, msFbo);
        gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, resolveFbo);
        gl.blitFramebuffer(0, 0, s.width, s.height, 0, 0, s.width, s.height, gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT, gl.NEAREST);
        await step(0.8, "Finishing the picture");
        // ---- post pass to the canvas: ambient occlusion and tone mapping only where the mode has them (Realistic); every mode is gamma-encoded
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, s.width, s.height);
        gl.disable(gl.DEPTH_TEST);
        gl.disable(gl.BLEND);
        gl.useProgram(postProg.prog);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, colourTex);
        gl.uniform1i(uni(gl, postProg, "u_color"), 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, depthTex);
        gl.uniform1i(uni(gl, postProg, "u_depth"), 1);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.NONE);
        gl.uniform2f(uni(gl, postProg, "u_size"), s.width, s.height);
        const ao = s.occlusion && plan.enhancements && meshes.length > 0;
        set1(postProg, "u_ao", ao ? 1 : 0);
        set1(postProg, "u_aoTaps", AO_TAPS[s.quality]);
        set1(postProg, "u_aoRadiusPx", Math.max(6, Math.min(s.width, s.height) * 0.012));
        set1(postProg, "u_aoRange", cam.ortho ? Math.max(radius * 0.06, 0.05) : Math.max(radius * 0.05, 0.05));
        set1(postProg, "u_aoStrength", 0.7);
        set1(postProg, "u_near", cam.ortho ? -far : near);
        set1(postProg, "u_far", far);
        set1(postProg, "u_ortho", cam.ortho ? 1 : 0);
        set1(postProg, "u_tone", s.toneMapping && plan.enhancements ? 1 : 0);
        const bg = s.background.kind === "paper" ? input.colours.paper : s.background.kind === "white" ? [1, 1, 1] : s.background.kind === "colour" ? parseHex(s.background.colour) : [1, 1, 1];
        gl.uniform4f(uni(gl, postProg, "u_background"), bg[0] ?? 1, bg[1] ?? 1, bg[2] ?? 1, 1);
        set1(postProg, "u_transparent", s.background.kind === "transparent" && s.format === "png" ? 1 : 0);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        gl.finish();
        await step(0.92, "Encoding the picture");
        // JPEG has no alpha: with a transparent background chosen, the post pass composed over white (transparency applies to PNG only)
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, s.format === "png" ? "image/png" : "image/jpeg", s.jpegQuality));
        if (!blob)
            throw new SnapshotUnavailable("The browser could not encode the picture. Try a smaller size.");
        if (hooks.signal?.aborted)
            throw new SnapshotCancelled();
        hooks.onProgress?.(1, "Done");
        return blob;
    }
    catch (error) {
        if (error instanceof GlUnavailable && !(error instanceof SnapshotUnavailable))
            throw new SnapshotUnavailable(error.message);
        throw error;
    }
    finally {
        release();
        canvas.width = 1;
        canvas.height = 1;
    }
}
/** The triangles of a mesh as interleaved position and flat normal, and its feature edges: the geometry the view itself draws. */
export function meshArrays(vertices, faces, edgePairs) {
    const pos = [];
    for (const f of faces) {
        const a = vertices[f[0] ?? -1], b = vertices[f[1] ?? -1], c = vertices[f[2] ?? -1];
        if (!a || !b || !c)
            continue;
        const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
        let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        const l = Math.hypot(nx, ny, nz) || 1;
        nx /= l;
        ny /= l;
        nz /= l;
        pos.push(a[0], a[1], a[2], nx, ny, nz, b[0], b[1], b[2], nx, ny, nz, c[0], c[1], c[2], nx, ny, nz);
    }
    const edges = [];
    for (const [p, q] of edgePairs) {
        const a = vertices[p], b = vertices[q];
        if (a && b)
            edges.push(...a, ...b);
    }
    return { positions: new Float32Array(pos), edges: new Float32Array(edges) };
}
/** A file name for a snapshot: ``<project>-3d-<width>x<height>.<ext>``, safe on every file system. */
export function snapshotFilename(project, s) {
    const base = project.replace(/[^\p{L}\p{N}._ -]+/gu, "").trim().replace(/\s+/g, "-").slice(0, 60) || "spadra";
    return `${base}-3d-${s.width}x${s.height}.${s.format === "png" ? "png" : "jpg"}`;
}
