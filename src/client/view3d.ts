/** The 3D model view: a WebGL renderer for the tessellated SGEO model, an orbit camera, picking, a view cube and wall placement.
 *  The geometry drawn is exactly what the server tessellated; nothing here invents shapes. */
import { h } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { toast } from "../../../tools/web/src/ui.js";
import { clamp } from "../../../tools/web/src/util.js";
import {
  cameraEye, cameraMatrices, centreOf, cross, featureEdges, HOME_VIEW, lookAround, normalise, orbitAbout, rayPlaneZ, rayThrough, rayTriangle, sub, transform, viewerDirection, walk, zoomAt,
  type Camera, type Mat4, type Ray,
} from "./math3d.js";
import { cssColor, rgba } from "./palette.js";
import { addLayerDialog } from "./dialogs.js";
import type { Store } from "./store.js";
import type { Pt3, Scene } from "./types.js";

const MM_PER = { mm: 1, cm: 10, m: 1000 } as const;
const MATERIAL_COLOR: Record<string, [number, number, number]> = {
  Concrete: [0.72, 0.73, 0.75], Timber: [0.70, 0.50, 0.30], Glass: [0.55, 0.75, 0.88], Masonry: [0.72, 0.42, 0.34], Steel: [0.50, 0.56, 0.64], Plasterboard: [0.92, 0.91, 0.88],
};
const FALLBACK_COLOR: [number, number, number] = [0.75, 0.75, 0.78];
const VOID_COLOR: [number, number, number] = [0.86, 0.30, 0.30];
const VOID_ALPHA = 0.3;
const PITCH_LIMIT = Math.PI / 2 - 0.02;

interface Drawable {
  id: string; name: string; material: string | null; color: string | null; layerColors: Array<{ count: number; color: string | null }>; category: string;
  ridgeHandle: { point: Pt3; across: [number, number]; offset_mm: number } | null;
  tri: WebGLBuffer; triCount: number; edge: WebGLBuffer; edgeCount: number;
  verts: Pt3[]; faces: number[][]; layerPoints: Array<{ side: string; point: Pt3 }>;
}

interface ElementDrag { id: string; start: Pt3; base: number[]; delta: number[]; y0: number }

export class ModelView {
  readonly el: HTMLElement;
  private readonly gl: WebGL2RenderingContext | null;
  private readonly canvas: HTMLCanvasElement;
  private readonly overlay: HTMLCanvasElement;
  private readonly octx: CanvasRenderingContext2D;
  private readonly hud: HTMLElement;
  private readonly layerControls = h("div", { class: "layer-controls", attrs: { "aria-label": "Add layer on selected faces" } });
  // Anchor only; this is not an actionable control or tab stop.
  private readonly contextAnchor = h("span", { attrs: { "aria-hidden": "true" } });
  private readonly levelSelect: HTMLSelectElement;
  private hudGroups: HTMLElement[] = [];
  private readonly fields: Record<string, HTMLInputElement | HTMLSelectElement> = {};
  private polyPts: Pt3[] = [];
  private openingAt: Pt3 | null = null;
  private pendingTool = "";
  private meshProgram: WebGLProgram | null = null;
  private lineProgram: WebGLProgram | null = null;
  private gridBuffer: WebGLBuffer | null = null;
  private gridCount = 0;
  private drawables: Drawable[] = [];
  private bounds: [Pt3, Pt3] | null = null;
  private sceneRevision = -1;
  private width = 1;
  private height = 1;
  private frame = 0;
  readonly cam: Camera = { ...HOME_VIEW, distance: 30, target: [0, 0, 0], ortho: true };
  private extent = 10;
  private panMode = false;
  private orbitButton: HTMLButtonElement | null = null;
  private gesture: { x: number; y: number; button: number; moved: boolean; pan: boolean; look: boolean; pivot: Pt3 | null; drag?: ElementDrag } | null = null;
  /** True only while a frame is being captured for export: no selection tint, hover or overlay. */
  private clean = false;
  private hover: string | null = null;
  private wallStart: Pt3 | null = null;
  private ground: Pt3 | null = null;
  private cubeFaces: Array<{ name: string; poly: Array<[number, number]>; yaw: number; pitch: number }> = [];
  private matrices: { view: Mat4; projection: Mat4; viewProjection: Mat4 } | null = null;

  constructor(private readonly store: Store) {
    this.canvas = h("canvas", { class: "view-canvas", attrs: { "aria-hidden": "true" } });
    this.overlay = h("canvas", { class: "view-overlay", attrs: { tabindex: "0", "aria-label": "3D model view" } });
    (this.overlay as HTMLCanvasElement & { spadraView?: ModelView }).spadraView = this;   // read by the browser tests (see screenOf)
    const octx = this.overlay.getContext("2d");
    if (!octx) throw new Error("This browser cannot draw on a canvas.");
    this.octx = octx;
    this.gl = this.canvas.getContext("webgl2", { antialias: true, alpha: false });
    this.levelSelect = h("select", { attrs: { "aria-label": "Level for new walls" } });
    const num = (key: string, label: string, value: string, min: string, step: string): HTMLLabelElement => {
      const input = h("input", { type: "number", value, min, step, attrs: { "aria-label": label } });
      this.fields[key] = input;
      return h("label", {}, label, input);
    };
    const pick = (key: string, label: string, options: Array<[string, string]>): HTMLLabelElement => {
      const select = h("select", { attrs: { "aria-label": label } }, ...options.map(([value, text]) => h("option", { value, text })));
      this.fields[key] = select;
      return h("label", {}, label, select);
    };
    const group = (tool: string, hint: string, ...inputs: HTMLElement[]): HTMLElement =>
      h("div", { class: "hud-group", hidden: true, data: { tool } }, ...inputs, h("span", { class: "muted", text: hint }));
    this.hudGroups = [
      group("wall", "Click two points. Shift keeps it straight. Esc ends.", num("wall.height", "Height (m)", "3.2", "0.1", "0.1"), num("wall.thickness", "Thickness (mm)", "250", "10", "10")),
      group("door", "Click on a wall to place a door.", num("door.width", "Width (mm)", "900", "100", "50"), num("door.height", "Height (mm)", "2100", "100", "50")),
      group("window", "Click on a wall to place a window.", num("window.width", "Width (mm)", "1200", "100", "50"), num("window.height", "Height (mm)", "1200", "100", "50"), num("window.sill", "Sill (mm)", "900", "0", "50")),
      group("floor", "Click the corners. Enter or double-click finishes, Esc cancels.", num("floor.thickness", "Thickness (mm)", "200", "10", "10")),
      group("roof", "Click the corners (four, at right angles, for a shed, gable or hip roof). Enter or double-click finishes. Change the type and the pitch later in Properties.", pick("roof.kind", "Roof", [["flat", "Flat"], ["shed", "Shed"], ["gable", "Gable"], ["hip", "Hip"]]),
        num("roof.pitch", "Pitch (°)", "30", "1", "1"), num("roof.thickness", "Thickness (mm)", "200", "10", "10")),
      group("section", "Click the two ends of the section line. It looks to the left of the line you draw. Esc cancels."),
      group("column", "Click where the column goes. Esc ends.", num("column.top", "Top above level (m)", "3.0", "0.1", "0.1"), num("column.width", "Width (mm)", "300", "10", "10"), num("column.depth", "Depth (mm)", "300", "10", "10")),
      group("beam", "Click two points. Esc ends. Profile sizes are nominal table sizes: check them against your supplier's table.", num("beam.top", "Top above level (m)", "3.0", "0.1", "0.1"),
        pick("beam.profile", "Profile", [["rect", "Rectangular"]]), pick("beam.size", "Size", [["", ""]]),
        num("beam.width", "Width (mm)", "200", "10", "10"), num("beam.depth", "Depth (mm)", "400", "10", "10"), num("beam.roll", "Roll (°)", "0", "-360", "90")),
    ];
    void this.loadProfiles();
    for (const [key, option] of [["wall.height", "height"], ["wall.thickness", "thickness"]] as const) {      // the wall options are shared with the plan's wall tool
      const input = this.fields[key];
      input?.addEventListener("input", () => { const n = Number(input.value); if (n > 0) this.store.wallOptions[option] = n; });
    }
    const roof = this.store.roofOptions;
    const roofKind = this.fields["roof.kind"];
    roofKind?.addEventListener("change", () => { roof.kind = roofKind.value as typeof roof.kind; });
    for (const [key, field] of [["roof.pitch", "pitch"], ["roof.thickness", "thickness"]] as const) {
      const input = this.fields[key];
      input?.addEventListener("input", () => { const n = Number(input.value); if (Number.isFinite(n) && n > 0) roof[field] = n; });
    }
    this.hud = h("div", { class: "wall-hud", hidden: true }, h("label", {}, "Level", this.levelSelect), ...this.hudGroups);
    const rail = h("div", { class: "view-rail", attrs: { role: "toolbar", "aria-label": "View tools" } },
      this.railButton("home", "Home view", () => { this.home(); }),
      this.railButton("search", "Zoom to selection", () => { this.zoomSelection(); }),
      this.railButton("pan", "Pan (hold the right mouse button, or toggle here)", (b) => { this.panMode = !this.panMode; b.setAttribute("aria-pressed", String(this.panMode)); }),
      this.orbitButton = this.railButton("orbit", "Orbit around the selected element (turn off to orbit around the point in the middle of the view)", () => { this.store.setOrbitSelection(!this.store.orbitSelection); }),
      this.railButton("fit", "Zoom to fit the whole model", () => { this.fit(); }));
    const unavailable = h("div", { class: "view-unavailable", hidden: !!this.gl, text: "3D view needs WebGL 2, which this browser does not provide. The model, properties and exports still work." });
    this.contextAnchor.style.cssText = "position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;padding:0;border:0";
    this.el = h("div", { class: "canvas-wrap" }, this.canvas, this.overlay, this.layerControls, this.hud, rail, unavailable, this.contextAnchor);
    if (this.gl) this.setup(this.gl);
    new ResizeObserver(() => this.resize()).observe(this.el);
    this.bind();
  }

  private railButton(icon: string, label: string, run: (button: HTMLButtonElement) => void): HTMLButtonElement {
    const button: HTMLButtonElement = h("button", { class: "iconbtn rail-btn", type: "button", attrs: { "aria-label": label, title: label }, on: { click: () => run(button) } }, glyph(icon));
    return button;
  }

  // ------------------------------------------------------------------------------------------------------------- GL setup
  private compile(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram | null {
    const make = (type: number, src: string): WebGLShader | null => {
      const sh = gl.createShader(type);
      if (!sh) return null;
      gl.shaderSource(sh, src); gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) { console.error(gl.getShaderInfoLog(sh)); return null; }
      return sh;
    };
    const v = make(gl.VERTEX_SHADER, vs), f = make(gl.FRAGMENT_SHADER, fs);
    const prog = gl.createProgram();
    if (!v || !f || !prog) return null;
    gl.attachShader(prog, v); gl.attachShader(prog, f); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.error(gl.getProgramInfoLog(prog)); return null; }
    return prog;
  }

  private setup(gl: WebGL2RenderingContext): void {
    this.meshProgram = this.compile(gl, `#version 300 es
      in vec3 a_pos; in vec3 a_normal; uniform mat4 u_vp; out vec3 v_normal;
      void main() { v_normal = a_normal; gl_Position = u_vp * vec4(a_pos, 1.0); }`, `#version 300 es
      precision highp float; in vec3 v_normal; uniform vec3 u_color; uniform vec3 u_tint; uniform float u_tintAmount; uniform float u_alpha; out vec4 o;
      void main() {
        vec3 n = normalize(v_normal); vec3 l = normalize(vec3(-0.45, -0.6, 0.75));
        float d = abs(dot(n, l)); float sky = 0.5 + 0.5 * abs(n.z);
        vec3 c = u_color * (0.58 + 0.30 * d + 0.18 * sky); c = mix(c, u_tint, u_tintAmount); o = vec4(c, u_alpha);
      }`);
    this.lineProgram = this.compile(gl, `#version 300 es
      in vec3 a_pos; uniform mat4 u_vp; void main() { gl_Position = u_vp * vec4(a_pos, 1.0); }`, `#version 300 es
      precision highp float; uniform vec4 u_color; out vec4 o; void main() { o = u_color; }`);
    this.gridBuffer = gl.createBuffer();
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);          // so edges drawn on the faces are not eaten by them
  }

  // ------------------------------------------------------------------------------------------------------------- scene
  setScene(scene: Scene | null): void {
    // Frame the model the first time it has content (a new project is empty, so the first wall would otherwise land off screen).
    // (While an animation exists the view can legitimately be empty for a moment, when everything is hidden; the camera must not jump then.)
    const first = this.sceneRevision < 0 || (this.drawables.length === 0 && !this.store.animation?.animation && (scene?.objects.length ?? 0) > 0);
    this.sceneRevision = scene?.revision ?? -1;
    this.disposeDrawables();
    this.bounds = scene?.bounds ?? null;
    const gl = this.gl;
    if (scene && gl) {
      for (const o of scene.objects) {
        const pos: number[] = [];
        for (const f of o.faces) {
          const [i, j, k] = f;
          const a = o.vertices[i ?? -1], b = o.vertices[j ?? -1], c = o.vertices[k ?? -1];
          if (!a || !b || !c) continue;
          const n = normalise(cross(sub(b, a), sub(c, a)));
          pos.push(...a, ...n, ...b, ...n, ...c, ...n);
        }
        const edges: number[] = [];
        for (const [p, q] of featureEdges(o.vertices, o.faces)) { const a = o.vertices[p], b = o.vertices[q]; if (a && b) edges.push(...a, ...b); }
        const tri = gl.createBuffer(), edge = gl.createBuffer();
        if (!tri || !edge) continue;
        gl.bindBuffer(gl.ARRAY_BUFFER, tri); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pos), gl.STATIC_DRAW);
        gl.bindBuffer(gl.ARRAY_BUFFER, edge); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(edges), gl.STATIC_DRAW);
        this.drawables.push({ id: o.id, name: o.name, material: o.material, color: o.color ?? null, layerColors: o.layer_colors ?? [], ridgeHandle: o.ridge_handle ?? null, category: o.category, tri, triCount: pos.length / 6, edge, edgeCount: edges.length / 3, verts: o.vertices, faces: o.faces, layerPoints: o.layer_points ?? [] });
      }
    }
    this.buildGrid();
    this.refreshLevels();
    if (first || !scene) this.fit();
    this.draw();
  }

  private disposeDrawables(): void {
    const gl = this.gl;
    if (gl) for (const d of this.drawables) { gl.deleteBuffer(d.tri); gl.deleteBuffer(d.edge); }
    this.drawables = [];
  }

  private refreshLevels(): void {
    const keep = this.levelSelect.value;
    while (this.levelSelect.firstChild) this.levelSelect.removeChild(this.levelSelect.firstChild);
    for (const l of this.store.levels) this.levelSelect.appendChild(h("option", { value: l.id, text: `${l.name} (${l.elevation} m)` }));
    if (keep && this.store.levels.some((l) => l.id === keep)) this.levelSelect.value = keep;
  }

  private buildGrid(): void {
    const gl = this.gl;
    if (!gl || !this.gridBuffer) return;
    const [lo, hi] = this.bounds ?? [[-10, -10, 0], [10, 10, 0]];
    const span = Math.max(hi[0] - lo[0], hi[1] - lo[1], 10);
    const step = span > 80 ? 10 : span > 30 ? 5 : span > 12 ? 2 : 1;
    const cx = Math.round((lo[0] + hi[0]) / 2 / step) * step, cy = Math.round((lo[1] + hi[1]) / 2 / step) * step;
    const half = Math.ceil((span * 0.9) / step) * step;
    const pts: number[] = [];
    for (let v = -half; v <= half; v += step) { pts.push(cx + v, cy - half, 0, cx + v, cy + half, 0, cx - half, cy + v, 0, cx + half, cy + v, 0); }
    this.gridCount = pts.length / 3;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.gridBuffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pts), gl.STATIC_DRAW);
  }

  // ------------------------------------------------------------------------------------------------------------ camera
  home(): void { this.cam.yaw = HOME_VIEW.yaw; this.cam.pitch = HOME_VIEW.pitch; this.fit(); }

  fit(): void {
    const [lo, hi] = this.bounds ?? [[-10, -10, 0], [10, 10, 3]];
    this.cam.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    const radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 1);
    this.extent = radius;
    this.cam.distance = radius * 2.9;
    this.draw();
  }

  zoomSelection(): void {
    const ids = this.selectedIds();
    const picked = this.drawables.filter((d) => ids.includes(d.id));
    if (!picked.length) { toast("Select something first, or use Zoom to fit.", { kind: "info" }); return; }
    const all = picked.flatMap((d) => d.verts);
    const lo: Pt3 = [Math.min(...all.map((v) => v[0])), Math.min(...all.map((v) => v[1])), Math.min(...all.map((v) => v[2]))];
    const hi: Pt3 = [Math.max(...all.map((v) => v[0])), Math.max(...all.map((v) => v[1])), Math.max(...all.map((v) => v[2]))];
    this.cam.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    this.cam.distance = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 0.5) * 2.9;
    this.draw();
  }

  /** Whether the browser gave this view a WebGL 2 context (frames cannot be rendered for export without it). */
  get hasWebGl(): boolean { return this.gl !== null; }

  setOrtho(ortho: boolean): void { this.cam.ortho = ortho; this.draw(); }

  private selectedIds(): string[] { return this.store.selection?.kind === "model" ? this.store.selection.ids : []; }

  // -------------------------------------------------------------------------------------------------------- interaction
  private pointer(e: MouseEvent): [number, number] {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  private rayAt(x: number, y: number): Ray | null {
    if (!this.matrices) return null;
    return rayThrough(this.matrices.viewProjection, (x / this.width) * 2 - 1, 1 - (y / this.height) * 2);
  }

  private bind(): void {
    const c = this.overlay;                                  // the overlay sits on top, so it receives the pointer
    c.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const [x, y] = this.pointer(e);
      const candidates = this.hitCandidates(x, y);
      // A wall and its foundation may share a ray. If the user has already
      // selected one of them, respect that selection for its context actions.
      const hit = candidates.find((candidate) => this.selectedIds().includes(candidate.id)) ?? candidates[0];
      if (!hit) return;
      const category = this.drawables.find((d) => d.id === hit.id)?.category;
      if (!category) return;
      this.placeContextAnchor(e.clientX, e.clientY);
      this.store.openModelContextMenu(hit.id, category, this.contextAnchor);
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [x, y] = this.pointer(e);
      zoomAt(this.cam, Math.exp(e.deltaY * 0.0015), (x / this.width) * 2 - 1, 1 - (y / this.height) * 2, this.width, this.height, this.extent);      // towards the pointer, not the middle
      this.draw();
    }, { passive: false });
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      c.focus();                                             // the overlay is what is clicked, so it takes the keyboard (Enter, Esc, Delete)
      const [x, y] = this.pointer(e);
      this.gesture = { x, y, button: e.button, moved: false, pan: this.panMode || e.button === 2 || e.button === 1, look: e.ctrlKey && !this.panMode && e.button === 0, pivot: this.orbitPivot() };
      this.beginElementDrag(this.gesture, x, y, e);
    });
    c.addEventListener("pointermove", (e) => this.move(e));
    c.addEventListener("pointerup", (e) => { void this.up(e); });
    c.addEventListener("pointerleave", () => { this.hover = null; this.ground = null; this.openingAt = null; this.draw(); });
    c.addEventListener("dblclick", () => { if (this.store.tool === "floor" || this.store.tool === "roof") void this.commitOutline(); });
    c.addEventListener("keydown", (e) => { void this.key(e); });
    this.canvas.addEventListener("keydown", (e) => { void this.key(e); });
  }

  private move(e: PointerEvent): void {
    const [x, y] = this.pointer(e);
    const g = this.gesture;
    if (g) {
      const dx = x - g.x, dy = y - g.y;
      if (!g.moved && Math.hypot(dx, dy) > 4) g.moved = true;
      if (g.drag) { if (g.moved) this.dragElement(g.drag, x, y, e.shiftKey); return; }
      if (g.moved) {
        if (g.pan) this.panBy(dx, dy); else if (g.look) lookAround(this.cam, dx * 0.008, -dy * 0.008, PITCH_LIMIT); else this.orbitBy(dx, dy, g.pivot);
        g.x = x; g.y = y;
        this.draw();
        return;
      }
    }
    const tool = this.store.tool;
    if (tool === "wall" || tool === "beam" || tool === "column" || tool === "floor" || tool === "roof" || tool === "section") this.ground = this.groundPoint(x, y, e.shiftKey);
    else if (tool === "door" || tool === "window") {
      const hit = this.hitAt(x, y, "wall");
      this.hover = hit?.id ?? null; this.openingAt = hit?.point ?? null;
    } else this.hover = this.pickAt(x, y);
    this.draw();
  }

  /** The point the camera turns about: the middle of the selected elements when "orbit around selection" is on, otherwise the point it looks at. */
  private orbitPivot(): Pt3 | null {
    if (!this.store.orbitSelection) return null;
    const ids = this.selectedIds();
    return centreOf(this.drawables.filter((d) => ids.includes(d.id)).flatMap((d) => d.verts));
  }

  private orbitBy(dx: number, dy: number, pivot: Pt3 | null = null): void {
    orbitAbout(this.cam, pivot ?? this.cam.target, -dx * 0.008, dy * 0.008, PITCH_LIMIT);
  }

  private panBy(dx: number, dy: number): void {
    const eye = cameraEye(this.cam);
    const forward = normalise(sub(this.cam.target, eye));
    const right = normalise(cross(forward, [0, 0, 1]));
    const up = cross(right, forward);
    const perPixel = (2 * this.cam.distance * Math.tan(Math.PI / 8)) / Math.max(1, this.height);
    for (let i = 0; i < 3; i++) this.cam.target[i] = (this.cam.target[i] ?? 0) - (right[i] ?? 0) * dx * perPixel + (up[i] ?? 0) * dy * perPixel;
  }

  private async up(e: PointerEvent): Promise<void> {
    const g = this.gesture;
    this.gesture = null;
    if (g?.drag) { await this.endElementDrag(g.drag, g.moved); return; }
    if (!g || g.moved || e.button !== 0) return;
    const [x, y] = this.pointer(e);
    const cube = this.cubeHit(x, y);
    if (cube) { this.cam.yaw = cube.yaw; this.cam.pitch = cube.pitch; this.draw(); return; }
    const tool = this.store.tool;
    if (tool === "wall") { await this.placeWall(this.groundPoint(x, y, e.shiftKey)); return; }
    if (tool === "beam") { await this.placeBeam(this.groundPoint(x, y, e.shiftKey)); return; }
    if (tool === "column") { await this.placeColumn(this.groundPoint(x, y, false)); return; }
    if (tool === "section") { await this.placeSection(this.groundPoint(x, y, e.shiftKey)); return; }
    if (tool === "floor" || tool === "roof") { const p = this.groundPoint(x, y, false); if (p) { this.polyPts.push(p); this.draw(); } return; }
    if (tool === "door" || tool === "window") { await this.placeOpening(tool, this.hitAt(x, y, "wall")); return; }
    const candidates = this.hitCandidates(x, y);
    // Alt+click cycles through *every* solid under this ray, including a wall
    // and its overlapping foundation. Nearest-surface selection is still the
    // default, but a concealed element remains selectable without hiding one.
    const current = this.selectedIds()[0];
    const currentIndex = candidates.findIndex((hit) => hit.id === current);
    const hit = e.altKey ? candidates[(currentIndex + 1) % candidates.length]?.id ?? null : candidates[0]?.id ?? null;
    if (candidates.length > 1) this.store.setStatus(`${candidates.length} overlapping elements: Alt+click cycles between them.`);
    if (hit && e.shiftKey) {
      const cur = this.selectedIds();
      const ids = cur.includes(hit) ? cur.filter((i) => i !== hit) : [...cur, hit];
      await this.store.select(ids.length ? { kind: "model", ids } : null);
    } else await this.store.select(hit ? { kind: "model", ids: [hit] } : null);
  }

  /** Move the camera from the keyboard: arrows orbit (Shift for bigger steps), Ctrl+arrows walk, Page Up and Page Down raise and lower it. */
  private navigate(e: KeyboardEvent): boolean {
    if (e.altKey || e.metaKey) return false;
    const step = this.cam.distance * 0.1 * (e.shiftKey ? 4 : 1);
    const turn = ((e.shiftKey ? 15 : 5) * Math.PI) / 180;
    if (e.key === "PageUp" || e.key === "PageDown") { walk(this.cam, 0, 0, e.key === "PageUp" ? step : -step); }
    else if (e.ctrlKey && e.key === "ArrowUp") walk(this.cam, step, 0, 0);
    else if (e.ctrlKey && e.key === "ArrowDown") walk(this.cam, -step, 0, 0);
    else if (e.ctrlKey && e.key === "ArrowLeft") walk(this.cam, 0, -step, 0);
    else if (e.ctrlKey && e.key === "ArrowRight") walk(this.cam, 0, step, 0);
    else if (e.ctrlKey) return false;
    else if (e.key === "ArrowLeft") orbitAbout(this.cam, this.orbitPivot() ?? this.cam.target, turn, 0, PITCH_LIMIT);
    else if (e.key === "ArrowRight") orbitAbout(this.cam, this.orbitPivot() ?? this.cam.target, -turn, 0, PITCH_LIMIT);
    else if (e.key === "ArrowUp") orbitAbout(this.cam, this.orbitPivot() ?? this.cam.target, 0, turn, PITCH_LIMIT);
    else if (e.key === "ArrowDown") orbitAbout(this.cam, this.orbitPivot() ?? this.cam.target, 0, -turn, PITCH_LIMIT);
    else return false;
    e.preventDefault();
    this.draw();
    return true;
  }

  private async key(e: KeyboardEvent): Promise<void> {
    if (e.key === "Escape") { if (this.wallStart || this.polyPts.length) { this.wallStart = null; this.polyPts = []; this.draw(); } else this.store.setTool("select"); return; }
    if (e.key === "Enter" && (this.store.tool === "floor" || this.store.tool === "roof")) { await this.commitOutline(); return; }
    if (e.key === "Home") { this.home(); return; }
    if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
      const id = this.selectedIds()[0], obj = this.drawables.find((d) => d.id === id);
      if (id && obj) {
        e.preventDefault();
        const box = this.overlay.getBoundingClientRect();
        this.placeContextAnchor(box.left + box.width / 2, box.top + box.height / 2);
        this.store.openModelContextMenu(id, obj.category, this.contextAnchor);
      }
      return;
    }
    if (this.navigate(e)) return;
    const ids = this.selectedIds();
    if ((e.key === "Delete" || e.key === "Backspace") && ids.length) {
      e.preventDefault();
      for (const id of ids) if (!(await this.store.run("model.delete", { id }))) break;
    }
  }

  // --------------------------------------------------------------------------------------------------------- drag to key
  /** With "Drag in 3D view" on, pressing on the selected element starts moving it for the key at the playhead instead of orbiting. */
  private beginElementDrag(g: NonNullable<ModelView["gesture"]>, x: number, y: number, e: PointerEvent): void {
    const sel = this.selectedIds();
    const pose = this.store.pose;
    if (!this.store.dragToKey || this.store.tool !== "select" || e.button !== 0 || sel.length !== 1 || !pose || pose.id !== sel[0]) return;
    const hit = this.hitAt(x, y);
    if (!hit || hit.id !== sel[0]) return;
    g.drag = { id: hit.id, start: hit.point, base: [...pose.pose.translation], delta: [0, 0, 0], y0: y };
  }

  private dragElement(d: ElementDrag, x: number, y: number, vertical: boolean): void {
    if (vertical) {
      const perPixel = (2 * this.cam.distance * Math.tan(Math.PI / 8)) / Math.max(1, this.height);
      d.delta = [0, 0, Math.round(((d.y0 - y) * perPixel) / 0.05) * 0.05];
    } else {
      const ray = this.rayAt(x, y);
      const p = ray ? rayPlaneZ(ray, d.start[2]) : null;
      if (p) d.delta = [Math.round((p[0] - d.start[0]) / 0.05) * 0.05, Math.round((p[1] - d.start[1]) / 0.05) * 0.05, 0];
    }
    this.draw();
  }

  private async endElementDrag(d: ElementDrag, moved: boolean): Promise<void> {
    this.draw();
    if (!moved || d.delta.every((v) => v === 0)) return;
    const translation = d.base.map((v, i) => Math.round((v + (d.delta[i] ?? 0)) * 1e6) / 1e6);
    await this.store.animate("pose", { id: d.id, time: this.store.animTime, values: { translation } });
  }

  /** The dragged element's box where it would be keyed, so the person sees the result before letting go. */
  private paintGhost(ctx: CanvasRenderingContext2D, font: string, select: string): void {
    const d = this.gesture?.drag;
    if (!d || d.delta.every((v) => v === 0)) return;
    const mesh = this.drawables.find((o) => o.id === d.id);
    if (!mesh || mesh.verts.length === 0) return;
    const lo: Pt3 = [Infinity, Infinity, Infinity], hi: Pt3 = [-Infinity, -Infinity, -Infinity];
    for (const v of mesh.verts) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i] ?? 0, v[i] ?? 0); hi[i] = Math.max(hi[i] ?? 0, v[i] ?? 0); }
    const corner = (i: number): Pt3 => [(i & 1 ? hi[0] : lo[0]) + (d.delta[0] ?? 0), (i & 2 ? hi[1] : lo[1]) + (d.delta[1] ?? 0), (i & 4 ? hi[2] : lo[2]) + (d.delta[2] ?? 0)];
    ctx.strokeStyle = select; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]);
    for (let i = 0; i < 8; i++) for (const bit of [1, 2, 4]) {
      if (i & bit) continue;
      const a = this.project(corner(i)), b = this.project(corner(i | bit));
      if (a && b) { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    }
    ctx.setLineDash([]);
    const at = this.project(corner(7));
    if (at) {
      const label = `Key at ${this.store.animTime.toFixed(2)} s: move ${d.delta.map((v) => v.toFixed(2)).join(", ")} m`;
      ctx.font = `12px ${font}`; const w = ctx.measureText(label).width + 10;
      ctx.fillStyle = this.css("--paper", "#fff"); ctx.fillRect(at[0] + 10, at[1] - 10, w, 20); ctx.strokeRect(at[0] + 10.5, at[1] - 9.5, w, 20);
      ctx.fillStyle = this.css("--ink", "#1a1d23"); ctx.textBaseline = "middle"; ctx.fillText(label, at[0] + 15, at[1]);
    }
  }

  // ------------------------------------------------------------------------------------------------------------ export frames
  /** Draw ``scene`` without selection, hover or overlay and return a copy of the picture, for the frame and video exports. */
  capture(scene: Scene): HTMLCanvasElement | null {
    if (!this.gl) return null;
    const saved = { ...this.cam, target: [...this.cam.target] as Pt3 };
    this.clean = true;
    try {
      this.setScene(scene);
      this.matrices = cameraMatrices(this.cam, this.width, this.height, this.extent);
      this.paintScene();                                                           // same task as the copy below: the GL buffer is still there
      const out = document.createElement("canvas");
      out.width = this.canvas.width; out.height = this.canvas.height;
      const ctx = out.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(this.canvas, 0, 0);
      return out;
    } finally {
      this.clean = false;
      Object.assign(this.cam, saved);                                               // framing the export must not move the person's camera
    }
  }


  /** Where the camera stands, in the terms a sheet's 3D view uses: degrees from +X counter-clockwise in plan, and degrees above the horizon (kept inside ±89). */
  cameraDirection(): { azimuth: number; elevation: number } { return viewerDirection(this.cam); }

  /** The picture the view shows now, without selection, hover or overlay, as PNG data (null where the browser has no WebGL 2). The view is drawn again afterwards. */
  snapshot(): string | null {
    if (!this.gl || this.width < 2 || this.height < 2) return null;
    this.clean = true;
    try {
      this.matrices = cameraMatrices(this.cam, this.width, this.height, this.extent);
      this.paintScene();                                                           // the copy below must happen in the same task: the GL buffer is still there
      const out = document.createElement("canvas");
      out.width = this.canvas.width; out.height = this.canvas.height;
      const ctx = out.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(this.canvas, 0, 0);
      return out.toDataURL("image/png");
    } finally {
      this.clean = false;
      this.draw();
    }
  }

  private pickAt(x: number, y: number): string | null { return this.hitAt(x, y)?.id ?? null; }

  private placeContextAnchor(clientX: number, clientY: number): void {
    const rect = this.el.getBoundingClientRect();
    this.contextAnchor.style.left = `${clientX - rect.left}px`;
    this.contextAnchor.style.top = `${clientY - rect.top}px`;
  }

  private hitCandidates(x: number, y: number, only?: string): Array<{ id: string; point: Pt3; distance: number }> {
    const ray = this.rayAt(x, y);
    if (!ray) return [];
    const hits: Array<{ id: string; point: Pt3; distance: number }> = [];
    for (const d of this.drawables) {
      if (only && d.category !== only) continue;
      let best = Infinity;
      for (const f of d.faces) {
        const a = d.verts[f[0] ?? -1], b = d.verts[f[1] ?? -1], c = d.verts[f[2] ?? -1];
        if (!a || !b || !c) continue;
        const t = rayTriangle(ray, a, b, c);
        if (t !== null && t < best) best = t;
      }
      if (Number.isFinite(best)) hits.push({ id: d.id, distance: best,
        point: [ray.origin[0] + ray.dir[0] * best, ray.origin[1] + ray.dir[1] * best, ray.origin[2] + ray.dir[2] * best] });
    }
    return hits.sort((a, b) => a.distance - b.distance || a.id.localeCompare(b.id));
  }

  /** The nearest thing under the pointer (optionally only of one category) and the exact point of the ray on it. */
  private hitAt(x: number, y: number, only?: string): { id: string; point: Pt3 } | null {
    return this.hitCandidates(x, y, only)[0] ?? null;
  }

  // ---------------------------------------------------------------------------------------------------------------- walls
  private levelElevation(): number { return this.store.levels.find((l) => l.id === this.levelSelect.value)?.elevation ?? 0; }

  /** The ground point under the pointer on the active level, snapped to a vertex of the model when one is near, else (when grid snap is on) to a 0.1 m grid. */
  private groundPoint(x: number, y: number, ortho: boolean): Pt3 | null {
    const ray = this.rayAt(x, y);
    const z = this.levelElevation();
    const hit = ray ? rayPlaneZ(ray, z) : null;
    if (!hit || !this.matrices) return null;
    let point: Pt3 = this.store.snapGrid ? [Math.round(hit[0] * 10) / 10, Math.round(hit[1] * 10) / 10, z] : [hit[0], hit[1], z];
    let best = 12;
    for (const d of this.drawables) for (const v of d.verts) {
      if (Math.abs(v[2] - z) > 0.01) continue;
      const [cx, cy, , w] = transform(this.matrices.viewProjection, v);
      if (w === 0) continue;
      const sx = ((cx / w + 1) / 2) * this.width, sy = ((1 - cy / w) / 2) * this.height;
      const dist = Math.hypot(sx - x, sy - y);
      if (dist < best) { best = dist; point = [v[0], v[1], z]; }
    }
    if (ortho && this.wallStart) point = Math.abs(point[0] - this.wallStart[0]) >= Math.abs(point[1] - this.wallStart[1]) ? [point[0], this.wallStart[1], z] : [this.wallStart[0], point[1], z];
    return point;
  }

  private async placeWall(p: Pt3 | null): Promise<void> {
    if (!p) return;
    if (!this.wallStart) { this.wallStart = p; this.draw(); return; }
    const start = this.wallStart;
    if (Math.hypot(p[0] - start[0], p[1] - start[1]) < 1e-6) return;
    const f = 1000 / MM_PER[this.store.unit];                    // metres to the project's drawing unit
    const height = this.num("wall.height"), thickness = this.num("wall.thickness");
    if (!(height > 0) || !(thickness > 0)) { toast("Enter a positive wall height and thickness.", { kind: "error" }); return; }
    const level = this.levelSelect.value;
    const out = await this.store.run("model.add_wall", { start: [start[0] * f, start[1] * f], end: [p[0] * f, p[1] * f], level, height, thickness });
    if (out) this.wallStart = p;                                // keep the chain going from the new end, as drawing tools do
    this.draw();
  }

  private num(key: string): number { return Number(this.fields[key]?.value); }

  /** A door or window where the pointer is on a wall. The server keeps it inside the wall and refuses overlaps. */
  private async placeOpening(kind: "door" | "window", hit: { id: string; point: Pt3 } | null): Promise<void> {
    if (!hit) { toast(`Click on a wall to place the ${kind}.`, { kind: "info" }); return; }
    const f = 1000 / MM_PER[this.store.unit];
    const params: Record<string, unknown> = { wall: hit.id, point: [hit.point[0] * f, hit.point[1] * f], width: this.num(`${kind}.width`), height: this.num(`${kind}.height`) };
    if (kind === "window") params["sill"] = this.num("window.sill");
    const out = await this.store.run(`model.add_${kind}`, params);
    if (out) { const id = (out.result as { id?: string } | undefined)?.id; if (id) await this.store.select({ kind: "model", ids: [id] }); }
  }

  /** A section along the line between two clicked points; it opens in its own tab. */
  private async placeSection(p: Pt3 | null): Promise<void> {
    if (!p) return;
    if (!this.wallStart) { this.wallStart = p; this.draw(); return; }
    const start = this.wallStart;
    if (Math.hypot(p[0] - start[0], p[1] - start[1]) < 0.01) return;
    const f = 1000 / MM_PER[this.store.unit];
    const out = await this.store.run("view.add_section", { a: [start[0] * f, start[1] * f], b: [p[0] * f, p[1] * f] });
    this.wallStart = null;
    this.draw();
    const made = out?.result as { id?: string; name?: string } | undefined;
    if (made?.id) await this.store.openTab({ kind: "drawing", id: made.id }, made.name ?? "Section");
  }

  /** A column centred on the clicked point of the active level. */
  private async placeColumn(p: Pt3 | null): Promise<void> {
    if (!p) return;
    const f = 1000 / MM_PER[this.store.unit];
    const out = await this.store.run("model.add_column", { point: [p[0] * f, p[1] * f], level: this.levelSelect.value,
      top: this.num("column.top"), width: this.num("column.width"), depth: this.num("column.depth") });
    const id = (out?.result as { id?: string } | undefined)?.id;
    if (id) await this.store.select({ kind: "model", ids: [id] });
    this.draw();
  }

  /** What the beam tool's boxes say about the section: a width and depth, or a profile family, its size and its roll. */
  private beamProfile(): Record<string, unknown> {
    const profile = (this.fields["beam.profile"] as HTMLSelectElement | undefined)?.value ?? "rect";
    if (profile === "rect") return { width: this.num("beam.width"), depth: this.num("beam.depth") };
    return { profile, size: (this.fields["beam.size"] as HTMLSelectElement | undefined)?.value, roll: this.num("beam.roll"), material: "Steel" };
  }

  /** The profile families and sizes the server offers for beams; the beam tool's Profile and Size boxes are filled from them. */
  private async loadProfiles(): Promise<void> {
    type Catalogue = { families: Array<{ key: string; label: string; default: string; sizes: Array<{ name: string }> }> };
    let cat: Catalogue;
    try { cat = await this.store.api.get<Catalogue>("/profiles"); } catch { return; }       // without the list the beam tool still draws rectangular beams
    const profile = this.fields["beam.profile"] as HTMLSelectElement | undefined, size = this.fields["beam.size"] as HTMLSelectElement | undefined;
    if (!profile || !size) return;
    for (const f of cat.families) profile.append(h("option", { value: f.key, text: f.label }));
    const show = (): void => {
      const rect = profile.value === "rect";
      for (const key of ["beam.width", "beam.depth"]) { const label = this.fields[key]?.parentElement; if (label) label.hidden = !rect; }
      for (const key of ["beam.size", "beam.roll"]) { const label = this.fields[key]?.parentElement; if (label) label.hidden = rect; }
      size.replaceChildren();
      const fam = cat.families.find((f) => f.key === profile.value);
      for (const sz of fam?.sizes ?? []) size.append(h("option", { value: sz.name, text: sz.name, selected: sz.name === fam?.default }));
    };
    profile.addEventListener("change", show);
    show();
  }

  /** A beam from two clicked points on the active level. */
  private async placeBeam(p: Pt3 | null): Promise<void> {
    if (!p) return;
    if (!this.wallStart) { this.wallStart = p; this.draw(); return; }
    const start = this.wallStart;
    if (Math.hypot(p[0] - start[0], p[1] - start[1]) < 1e-6) return;
    const f = 1000 / MM_PER[this.store.unit];
    const out = await this.store.run("model.add_beam", { start: [start[0] * f, start[1] * f], end: [p[0] * f, p[1] * f], level: this.levelSelect.value,
      top: this.num("beam.top"), ...(this.beamProfile()) });
    if (out) this.wallStart = null;
    this.draw();
  }

  /** Finish a floor or roof outline from the clicked corners. */
  private async commitOutline(): Promise<void> {
    const tool = this.store.tool;
    const pts = this.polyPts.filter((p, i) => i === 0 || Math.hypot(p[0] - (this.polyPts[i - 1]?.[0] ?? p[0]), p[1] - (this.polyPts[i - 1]?.[1] ?? p[1])) > 1e-6);
    if (pts.length < 3) { toast("An outline needs at least three corners.", { kind: "info" }); return; }
    const f = 1000 / MM_PER[this.store.unit];
    const points = pts.map((p) => [p[0] * f, p[1] * f]);
    const level = this.levelSelect.value;
    const out = tool === "floor"
      ? await this.store.run("model.add_floor", { points, level, thickness: this.num("floor.thickness") })
      : await this.store.run("model.add_roof", { points, level, roof: this.store.roofOptions.kind, pitch: this.store.roofOptions.pitch, thickness: this.store.roofOptions.thickness });
    if (out) this.polyPts = [];
    this.draw();
  }

  // --------------------------------------------------------------------------------------------------------------- render
  private resize(): void {
    const r = this.el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.width = Math.max(1, Math.round(r.width)); this.height = Math.max(1, Math.round(r.height));
    for (const cv of [this.canvas, this.overlay]) {
      cv.width = Math.round(this.width * dpr); cv.height = Math.round(this.height * dpr);
      cv.style.setProperty("width", `${this.width}px`); cv.style.setProperty("height", `${this.height}px`);
    }
    this.octx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  draw(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.paint(); });
  }

  private css(name: string, fallback: string): string { return name === "--font-ui" ? getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback : cssColor(name, fallback); }

  private paint(): void {
    const tool = this.store.tool;
    const hudTool = ["wall", "door", "window", "floor", "roof", "beam", "column", "section"].includes(tool);
    this.hud.hidden = !hudTool;
    this.orbitButton?.setAttribute("aria-pressed", String(this.store.orbitSelection));
    for (const g of this.hudGroups) g.hidden = g.dataset["tool"] !== tool;
    if (tool === "wall") for (const [key, option] of [["wall.height", "height"], ["wall.thickness", "thickness"]] as const) {
      const input = this.fields[key];
      if (input && document.activeElement !== input && Number(input.value) !== this.store.wallOptions[option]) input.value = String(this.store.wallOptions[option]);
    }
    if (tool === "roof") for (const [key, value] of [["roof.kind", this.store.roofOptions.kind], ["roof.pitch", this.store.roofOptions.pitch], ["roof.thickness", this.store.roofOptions.thickness]] as const) {
      const input = this.fields[key];
      if (input && document.activeElement !== input && input.value !== String(value)) input.value = String(value);
    }
    if (tool !== this.pendingTool) { this.wallStart = null; this.polyPts = []; this.openingAt = null; this.pendingTool = tool; }
    this.matrices = cameraMatrices(this.cam, this.width, this.height, this.extent);
    this.paintScene();
    this.paintOverlay();
    this.syncLayerControls();
  }

  private syncLayerControls(): void {
    this.layerControls.replaceChildren();
    const selection = this.store.selection;
    if (this.clean || this.store.tool !== "select" || selection?.kind !== "model" || selection.ids.length !== 1) return;
    const id = selection.ids[0] as string;
    const pipe = this.drawables.find((d) => d.id === id && d.category === "downpipe");
    if (pipe) { this.syncDownpipeGrip(pipe); return; }
    const item = this.drawables.find((d) => d.id === id && ["wall", "floor", "roof"].includes(d.category));
    if (!item) return;
    for (const { side, point } of item.layerPoints) {
      const pos = this.project(point);
      if (!pos || pos[0] < 8 || pos[1] < 8 || pos[0] > this.width - 8 || pos[1] > this.height - 8) continue;
      const button = h("button", { class: "layer-plus", type: "button", text: "+", attrs: { title: `Add ${side} ${item.category} layer`, "aria-label": `Add ${side} ${item.category} layer` },
        on: { click: (event) => { event.stopPropagation(); void addLayerDialog(this.store, id, item.category as "wall" | "floor" | "roof", side); } } });
      button.style.left = `${pos[0]}px`; button.style.top = `${pos[1]}px`;
      this.layerControls.appendChild(button);
    }
    if (item.ridgeHandle) {
      const { point, across, offset_mm } = item.ridgeHandle;
      const pos = this.project(point);
      const transverse = this.project([point[0] + across[0], point[1] + across[1], point[2]]);
      if (pos && transverse && pos[0] >= 8 && pos[1] >= 8 && pos[0] <= this.width - 8 && pos[1] <= this.height - 8) {
        const vx = transverse[0] - pos[0], vy = transverse[1] - pos[1];
        const norm2 = vx * vx + vy * vy;
        const grip = h("button", { class: "ridge-grip", type: "button", text: "◆",
          attrs: { title: "Drag the gable ridge sideways without changing its elevation", "aria-label": "Move roof ridge" } });
        grip.style.left = `${pos[0]}px`; grip.style.top = `${pos[1]}px`;
        let origin: [number, number] | null = null;
        let delta = 0;
        grip.addEventListener("pointerdown", e => { if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); grip.setPointerCapture(e.pointerId); origin = [e.clientX, e.clientY]; });
        grip.addEventListener("pointermove", e => {
          if (!origin || norm2 < 1e-8) return;
          delta = ((e.clientX - origin[0]) * vx + (e.clientY - origin[1]) * vy) / norm2;
          grip.style.left = `${pos[0] + vx * delta}px`; grip.style.top = `${pos[1] + vy * delta}px`;
          grip.title = `Ridge offset ${(offset_mm + delta * 1000).toFixed(0)} mm (release to apply)`;
        });
        grip.addEventListener("pointerup", e => {
          if (!origin) return;
          origin = null; grip.releasePointerCapture(e.pointerId);
          if (Math.abs(delta) > 1e-5) void this.store.run("model.set_property", { id, key: "ridge_offset", value: offset_mm + delta * 1000 });
        });
        grip.addEventListener("pointercancel", () => { origin = null; delta = 0; grip.style.left = `${pos[0]}px`; grip.style.top = `${pos[1]}px`; });
        this.layerControls.appendChild(grip);
      }
    }
  }

  /** Direct downpipe-length edit: only the free bottom endpoint moves.
   *  Projected world Z supplies the drag axis; the gutter-attached top never
   *  changes. The guide is temporary and the backend validates the final mesh. */
  private syncDownpipeGrip(item: Drawable): void {
    if (!item.verts.length) return;
    const z0 = Math.min(...item.verts.map(v => v[2]));
    const z1 = Math.max(...item.verts.map(v => v[2]));
    const x0 = Math.min(...item.verts.map(v => v[0])), x1 = Math.max(...item.verts.map(v => v[0]));
    const y0 = Math.min(...item.verts.map(v => v[1])), y1 = Math.max(...item.verts.map(v => v[1]));
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const bottom = this.project([cx, cy, z0]), top = this.project([cx, cy, z1]);
    const unit = this.project([cx, cy, z0 + 1]);
    if (!bottom || !top || !unit || bottom[0] < 8 || bottom[1] < 8 ||
        bottom[0] > this.width - 8 || bottom[1] > this.height - 8) return;
    const vertical: [number, number] = [unit[0] - bottom[0], unit[1] - bottom[1]];
    const norm2 = vertical[0] ** 2 + vertical[1] ** 2;
    if (norm2 < 1e-6) return; // End-on view: use numerical Length in Properties instead.
    const grip = h("button", { class: "ridge-grip", type: "button", text: "◆",
      attrs: { "aria-label": "Adjust downpipe length", title: "Drag the free bottom endpoint; the gutter connection stays fixed" } });
    grip.style.left = `${bottom[0]}px`; grip.style.top = `${bottom[1]}px`;
    const guide = h("div", { attrs: { "aria-hidden": "true" } });
    guide.style.cssText = "display:none;position:absolute;pointer-events:none;border-top:2px dashed #FFD400;transform-origin:left center";
    this.layerControls.append(guide, grip);
    let origin: [number, number] | null = null;
    let length = z1 - z0;
    const paintGuide = (point: [number, number]): void => {
      const dx = point[0] - top[0], dy = point[1] - top[1];
      guide.style.display = "";
      guide.style.left = `${top[0]}px`; guide.style.top = `${top[1]}px`;
      guide.style.width = `${Math.hypot(dx, dy)}px`;
      guide.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
      grip.style.left = `${point[0]}px`; grip.style.top = `${point[1]}px`;
    };
    grip.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation(); grip.setPointerCapture(e.pointerId);
      origin = [e.clientX, e.clientY];
      length = z1 - z0;
    });
    grip.addEventListener("pointermove", e => {
      if (!origin) return;
      const dz = ((e.clientX - origin[0]) * vertical[0] + (e.clientY - origin[1]) * vertical[1]) / norm2;
      const raw = Math.max(0.1, (z1 - z0) - dz);
      length = e.shiftKey ? Math.round(raw * 10) / 10 : raw;
      const actualDz = (z1 - z0) - length;
      paintGuide([bottom[0] + actualDz * vertical[0], bottom[1] + actualDz * vertical[1]]);
      grip.title = `Downpipe length ${(length * 1000).toFixed(0)} mm — release to apply (Shift: 100 mm steps)`;
    });
    grip.addEventListener("pointerup", e => {
      if (!origin) return;
      origin = null; grip.releasePointerCapture(e.pointerId);
      guide.style.display = "none";
      if (Math.abs(length - (z1 - z0)) > 1e-5)
        void this.store.run("model.set_property", { id: item.id, key: "length", value: length });
      else { grip.style.left = `${bottom[0]}px`; grip.style.top = `${bottom[1]}px`; }
    });
    grip.addEventListener("pointercancel", () => {
      origin = null; guide.style.display = "none";
      grip.style.left = `${bottom[0]}px`; grip.style.top = `${bottom[1]}px`;
    });
  }

  private parseColor(value: string): [number, number, number, number] { return rgba(value); }

  private paintScene(): void {
    const gl = this.gl, m = this.matrices;
    if (!gl || !m || !this.meshProgram || !this.lineProgram) return;
    const dpr = window.devicePixelRatio || 1;
    gl.viewport(0, 0, Math.round(this.width * dpr), Math.round(this.height * dpr));
    const [pr, pg, pb] = this.parseColor(this.css("--paper", "#ffffff"));
    gl.clearColor(pr, pg, pb, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const ink = this.parseColor(this.css("--ink", "#1a1d23"));
    const select = this.parseColor(this.css("--select", "#1a5fd1"));
    const gridColor = this.parseColor(this.css("--grid-major", "#cfd4dc"));
    const vp = m.viewProjection;

    const line = this.lineProgram; gl.useProgram(line);
    gl.uniformMatrix4fv(gl.getUniformLocation(line, "u_vp"), false, vp);
    const aPos = gl.getAttribLocation(line, "a_pos"), uColor = gl.getUniformLocation(line, "u_color");
    gl.bindBuffer(gl.ARRAY_BUFFER, this.gridBuffer);
    gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
    gl.uniform4f(uColor, gridColor[0], gridColor[1], gridColor[2], 1);
    gl.drawArrays(gl.LINES, 0, this.gridCount);

    const mesh = this.meshProgram; gl.useProgram(mesh);
    gl.uniformMatrix4fv(gl.getUniformLocation(mesh, "u_vp"), false, vp);
    const mPos = gl.getAttribLocation(mesh, "a_pos"), mNor = gl.getAttribLocation(mesh, "a_normal");
    const ids = new Set(this.clean ? [] : this.selectedIds());
    const hover = this.clean ? null : this.hover;
    const paintSolids = (voids: boolean): void => {
      for (const d of this.drawables) {
        if ((d.category === "void") !== voids) continue;
        gl.uniform3f(gl.getUniformLocation(mesh, "u_tint"), select[0], select[1], select[2]);
        gl.uniform1f(gl.getUniformLocation(mesh, "u_tintAmount"), ids.has(d.id) ? 0.45 : d.id === hover ? 0.18 : 0);
        gl.uniform1f(gl.getUniformLocation(mesh, "u_alpha"), voids ? VOID_ALPHA : 1);
        gl.bindBuffer(gl.ARRAY_BUFFER, d.tri);
        gl.enableVertexAttribArray(mPos); gl.vertexAttribPointer(mPos, 3, gl.FLOAT, false, 24, 0);
        gl.enableVertexAttribArray(mNor); gl.vertexAttribPointer(mNor, 3, gl.FLOAT, false, 24, 12);
        const parts = d.layerColors.length && d.layerColors.reduce((n, x) => n + x.count * 3, 0) === d.triCount
          ? d.layerColors.map(x => ({ count: x.count * 3, color: x.color })) : [{ count: d.triCount, color: d.color }];
        let at = 0;
        for (const part of parts) {
          const hex = part.color && /^#[0-9a-f]{6}$/i.test(part.color) ? part.color : null;
          const base: [number, number, number] = voids ? VOID_COLOR : hex
            ? [parseInt(hex.slice(1, 3), 16) / 255, parseInt(hex.slice(3, 5), 16) / 255, parseInt(hex.slice(5, 7), 16) / 255]
            : MATERIAL_COLOR[d.material ?? ""] ?? FALLBACK_COLOR;
          gl.uniform3f(gl.getUniformLocation(mesh, "u_color"), base[0], base[1], base[2]);
          gl.drawArrays(gl.TRIANGLES, at, part.count);
          at += part.count;
        }
      }
    };
    paintSolids(false);
    if (this.drawables.some((d) => d.category === "void")) {                       // a void is drawn see-through, over the solids it cuts
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
      paintSolids(true);
      gl.depthMask(true); gl.disable(gl.BLEND);
    }
    gl.disableVertexAttribArray(mNor);

    gl.useProgram(line);
    gl.enableVertexAttribArray(aPos);
    for (const d of this.drawables) {
      const c = ids.has(d.id) ? select : ink;
      gl.uniform4f(uColor, c[0], c[1], c[2], 1);
      gl.bindBuffer(gl.ARRAY_BUFFER, d.edge);
      gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.LINES, 0, d.edgeCount);
    }
  }

  private project(p: Pt3): [number, number] | null {
    if (!this.matrices) return null;
    const [x, y, , w] = transform(this.matrices.viewProjection, p);
    if (w <= 0) return null;
    return [((x / w + 1) / 2) * this.width, ((1 - y / w) / 2) * this.height];
  }

  private paintOverlay(): void {
    const ctx = this.octx;
    ctx.clearRect(0, 0, this.width, this.height);
    const font = this.css("--font-ui", "sans-serif");
    const ink = this.css("--ink", "#1a1d23"), select = this.css("--select", "#1a5fd1");
    this.paintPlacement(ctx, font, select);
    // wall rubber band
    if ((this.store.tool === "wall" || this.store.tool === "beam" || this.store.tool === "column" || this.store.tool === "section") && this.ground) {
      const g = this.project(this.ground);
      if (g) { ctx.strokeStyle = select; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(g[0], g[1], 5, 0, Math.PI * 2); ctx.stroke(); }
      if (this.wallStart && g) {
        const s = this.project(this.wallStart);
        if (s) {
          ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(s[0], s[1]); ctx.lineTo(g[0], g[1]); ctx.stroke(); ctx.setLineDash([]);
          const len = Math.hypot(this.ground[0] - this.wallStart[0], this.ground[1] - this.wallStart[1]);
          ctx.font = `12px ${font}`; const label = `${len.toFixed(2)} m`; const w = ctx.measureText(label).width + 10;
          ctx.fillStyle = this.css("--paper", "#fff"); ctx.fillRect(g[0] + 12, g[1] + 12, w, 20); ctx.strokeRect(g[0] + 12.5, g[1] + 12.5, w, 20);
          ctx.fillStyle = ink; ctx.textBaseline = "middle"; ctx.fillText(label, g[0] + 17, g[1] + 22.5);
        }
      }
    }
    this.paintGhost(ctx, font, select);
    const turning = this.gesture;
    if (turning?.moved && !turning.pan && !turning.look && turning.pivot) {                    // show what the view is turning about
      const at = this.project(turning.pivot);
      if (at) { ctx.strokeStyle = select; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(at[0], at[1], 6, 0, Math.PI * 2); ctx.moveTo(at[0] - 10, at[1]); ctx.lineTo(at[0] + 10, at[1]); ctx.moveTo(at[0], at[1] - 10); ctx.lineTo(at[0], at[1] + 10); ctx.stroke(); }
    }
    this.paintAxes(ctx, font);
    this.paintCube(ctx, font, ink);
  }

  /** Feedback for the floor, roof and opening tools: the outline so far, and where a door or window would land. */
  private paintPlacement(ctx: CanvasRenderingContext2D, font: string, color: string): void {
    const tool = this.store.tool;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.5;
    if ((tool === "floor" || tool === "roof") && (this.polyPts.length || this.ground)) {
      const pts = [...this.polyPts, ...(this.ground ? [this.ground] : [])].map((p) => this.project(p)).filter((q): q is [number, number] => !!q);
      ctx.setLineDash([6, 4]); ctx.beginPath();
      pts.forEach((q, i) => { if (i) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
      if (this.polyPts.length > 1) ctx.lineTo((pts[0] ?? [0, 0])[0], (pts[0] ?? [0, 0])[1]);
      ctx.stroke(); ctx.setLineDash([]);
      for (const q of pts) { ctx.beginPath(); ctx.arc(q[0], q[1], 3.5, 0, Math.PI * 2); ctx.fill(); }
    }
    if ((tool === "door" || tool === "window") && this.openingAt) {
      const q = this.project(this.openingAt);
      if (q) { ctx.beginPath(); ctx.arc(q[0], q[1], 6, 0, Math.PI * 2); ctx.stroke(); ctx.font = `12px ${font}`; ctx.textBaseline = "middle"; ctx.fillText(tool === "door" ? "Door" : "Window", q[0] + 12, q[1]); }
    }
  }

  /** The XYZ triad, bottom-left: the real axes of the model, rotated with the camera. */
  private paintAxes(ctx: CanvasRenderingContext2D, font: string): void {
    if (!this.matrices) return;
    const o: [number, number] = [56, this.height - 56];
    const axes: Array<[string, Pt3, string]> = [["X", [1, 0, 0], "#e0452f"], ["Y", [0, 1, 0], "#2fa84f"], ["Z", [0, 0, 1], "#2f6de0"]];
    const view = this.matrices.view;
    ctx.lineWidth = 2; ctx.font = `bold 12px ${font}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const [name, v, color] of axes) {
      const [x, y] = transform(view, [v[0], v[1], v[2]]);
      const [x0, y0] = transform(view, [0, 0, 0]);
      const dx = (x - x0) * 34, dy = -(y - y0) * 34;
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(o[0], o[1]); ctx.lineTo(o[0] + dx, o[1] + dy); ctx.stroke();
      ctx.fillText(name, o[0] + dx * 1.3, o[1] + dy * 1.3);
    }
  }

  /** The view cube, top-right. Click a face to look straight at it. */
  private paintCube(ctx: CanvasRenderingContext2D, font: string, ink: string): void {
    if (!this.matrices) return;
    const cx = this.width - 70, cy = 74, s = 24;
    const rot = this.matrices.view;
    const faces: Array<{ name: string; n: Pt3; u: Pt3; v: Pt3; yaw: number; pitch: number }> = [
      { name: "Front", n: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1], yaw: 0, pitch: 0 },
      { name: "Back", n: [0, 1, 0], u: [-1, 0, 0], v: [0, 0, 1], yaw: Math.PI, pitch: 0 },
      { name: "Right", n: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1], yaw: Math.PI / 2, pitch: 0 },
      { name: "Left", n: [-1, 0, 0], u: [0, -1, 0], v: [0, 0, 1], yaw: -Math.PI / 2, pitch: 0 },
      { name: "Top", n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], yaw: 0, pitch: PITCH_LIMIT },
      { name: "Bottom", n: [0, 0, -1], u: [1, 0, 0], v: [0, -1, 0], yaw: 0, pitch: -PITCH_LIMIT },
    ];
    const rotate = (p: Pt3): [number, number, number] => {
      const g = (i: number): number => rot[i] ?? 0;
      return [g(0) * p[0] + g(4) * p[1] + g(8) * p[2], g(1) * p[0] + g(5) * p[1] + g(9) * p[2], g(2) * p[0] + g(6) * p[1] + g(10) * p[2]];
    };
    const visible = faces.map((f) => ({ f, depth: rotate(f.n)[2] })).filter((x) => x.depth > 0.01).sort((a, b) => a.depth - b.depth);
    this.cubeFaces = [];
    ctx.font = `600 10px ${font}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const { f, depth } of visible) {
      const corner = (su: number, sv: number): [number, number] => {
        const w: Pt3 = [f.n[0] + su * f.u[0] + sv * f.v[0], f.n[1] + su * f.u[1] + sv * f.v[1], f.n[2] + su * f.u[2] + sv * f.v[2]];
        const r = rotate(w);
        return [cx + r[0] * s, cy - r[1] * s];
      };
      const poly: Array<[number, number]> = [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)];
      this.cubeFaces.push({ name: f.name, poly, yaw: f.yaw, pitch: f.pitch });
      ctx.beginPath(); poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath();
      ctx.fillStyle = `rgba(255,255,255,${0.55 + 0.35 * depth})`; ctx.fill();
      ctx.strokeStyle = this.css("--border", "#999999"); ctx.lineWidth = 1; ctx.stroke();
      const c = corner(0, 0);
      ctx.fillStyle = ink; ctx.fillText(f.name, c[0], c[1]);
    }
    this.cubeFaces.reverse();                                       // hit-test the nearest face first
  }

  private cubeHit(x: number, y: number): { yaw: number; pitch: number } | null {
    for (const f of this.cubeFaces) {
      let inside = false;
      for (let i = 0, j = f.poly.length - 1; i < f.poly.length; j = i++) {
        const [xi, yi] = f.poly[i] as [number, number], [xj, yj] = f.poly[j] as [number, number];
        if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
      }
      if (inside) return f;
    }
    return null;
  }

  /** For tests: how many triangles are being drawn, and the camera, without touching GL state. */
  get stats(): { objects: number; triangles: number; webgl: boolean } {
    return { objects: this.drawables.length, triangles: this.drawables.reduce((n, d) => n + d.triCount / 3, 0), webgl: !!this.gl };
  }
  /** Where a world point is on screen, for tests that click on model geometry. */
  screenOf(p: Pt3): [number, number] | null { return this.project(p); }
}
