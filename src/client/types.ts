/** The shapes the SPADRA server returns. They mirror project.py; a field is typed here only when the server really sends it. */

export type Pt = [number, number];
export type Pt3 = [number, number, number];

export interface ProjectSummary {
  id: string; name: string; unit: "mm" | "cm" | "m"; revision: number; dirty: boolean; path: string | null;
  can_undo: boolean; can_redo: boolean; undo_label: string | null; warnings: string[];
  /** Whether rooms are made on their own wherever walls close a space. */
  auto_rooms: boolean;
}
export interface Level { id: string; name: string; elevation: number }
export interface View {
  id: string; kind: "3d" | "plan" | "section" | "elevation" | "schedule"; name: string; level_id?: string;
  side?: string; category?: string; a?: Pt; b?: Pt; depth?: number;
}

export type OpenRef = { kind: "3d" | "drawing" | "sheet" | "schedule"; id: string; timeline?: boolean };
/** One row of the Project Browser. What clicking it does is stated by the server: open a view, select an element, open a dialog, or show a report. */
export interface TreeNode {
  id: string; label: string; detail?: string; view_kind?: View["kind"];
  open?: OpenRef; select?: { model: string };
  /** A dialog to open (``families``) and what it is about. */
  dialog?: string; ref?: string;
  /** A report to read. */
  report?: string;
  /** ``changed``: a sheet that differs from its last issue. */
  flag?: string | null;
  children?: TreeNode[];
}
export interface TreeResponse { project: ProjectSummary; tree: { sections: TreeNode[] }; levels: Level[] }

export interface Capabilities {
  drawing2d: boolean; model3d: boolean; walls: boolean; sheets: string | boolean; exportSvg: boolean; exportGlb: boolean; exportStl: boolean;
  sections: boolean; elevations: boolean; schedules: boolean; animation: boolean; engineering: boolean; assistant: boolean; simulation: boolean;
  optimization?: boolean; spatial?: boolean; calculator?: boolean;
}
/** What a failure means to the person (served once by the server, so the screen has no table of its own). */
export interface ErrorInfo { category: string; recovery: string }
export interface Bootstrap {
  app: { name: string; version: string; slai: unknown };
  project: ProjectSummary | null;
  recent: Array<{ path: string; name: string }>;
  units: string[];
  capabilities: Capabilities;
  storage: { dataDir: string; projectsDir: string };
  errors?: Record<string, ErrorInfo>;
  jobs?: string[];
}

export type Geometry =
  | { start: Pt; end: Pt }
  | { points: Pt[]; closed: boolean }
  | { center: Pt; radius: number }
  | { dim_type: string; p1: Pt; p2: Pt; dimline_point: Pt }
  | { position: Pt; text: string; height: number; rotation: number; halign: string; valign: string };

export interface Primitive { kind: string; start?: Pt; end?: Pt; [k: string]: unknown }
export interface Entity {
  id: string; kind: "line" | "polyline" | "circle" | "dimension" | "text" | string; layer: string;
  geometry: Record<string, unknown>; primitives?: Primitive[]; text?: string; strokes?: Pt[][];
}
export interface Layer { name: string; visible: boolean; entity_count: number }
/** What the model shows on a plan: a wall piece, a door or window (with its symbol), a floor or a beam, as a polygon in drawing units. */
export interface PlanSymbol {
  kind: "door" | "window"; hinge?: Pt; leaf_end?: Pt; radius?: number; start_angle?: number; end_angle?: number; a?: Pt; b?: Pt;
  /** The other swinging panels of a door that has several: each with its own hinge, open leaf and arc. */
  more?: Array<{ hinge: Pt; leaf_end: Pt; radius: number; start_angle: number; end_angle: number }>;
}
/** A room's plan label: its name and number, its area from the outline (m²) and where the label sits (drawing units). */
export interface RoomLabel { name: string; number: string; area_m2: number; volume_m3?: number; at: Pt }
/** A grid line's bubble: its label, where the bubble sits (the line's start) and its radius, in drawing units. */
/** A grid's label: where its bubble(s) are drawn (`bubbles` names the ends that carry one; an older answer without it means the start). */
export interface GridLabel { name: string; at: Pt; radius: number; bubbles?: Array<"start" | "end"> }
/** A stair's drawn parts: a line across every tread, and the arrow along its run (labelled UP). */
export interface StairArrow { from: Pt; to: Pt; label: string }
export interface PlanWall {
  id: string; category: string; name: string; polygon: Pt[]; symbol?: PlanSymbol; cut?: boolean; label?: RoomLabel | GridLabel;
  lines?: Array<[Pt, Pt]>; arrow?: StairArrow; hatch?: HatchSpec | null;
  /** A layered wall in plan: each layer's band, in pieces cut around the openings that layer inherits. */
  bands?: Array<{ layer: string; name: string; material: string; polygons: Pt[][]; hatch?: HatchSpec | null }>;
  /** Rings cut out of the polygon (a floor or extrusion with voids through it, or the inside of a closed sweep) and a sweep's centre line. */
  holes?: Pt[][]; path?: Pt[]; closed?: boolean; /** a void whose cutting is off is drawn dotted and unhatched */ off?: boolean;
  /** A wall's centre line and thickness, in the drawing's unit: what its end grips hold and what the next wall snaps to. */
  axis?: { a: Pt; b: Pt; thickness: number };
  /** A floor's outline (its corner points, in the drawing's unit): what its corner and edge grips hold. */
  outline?: Pt[];
  /** A foundation's kind (poer, strook, plaat...). */
  kind?: string;
}
/** One element as a section or elevation shows it: surfaces facing the viewer (flat triangles), their drawn lines, and the cut. Units are the drawing's. */
/** `cut_material` (a foundation only): the material of each loop of `cut`, so a build-up is hatched layer by layer. */
/** How a cut material is hatched (assigned per project; SPADRA's configurable patterns, not NEN 47). `spacing_mm` is on paper. */
export interface HatchSpec { pattern: string; label: string; kind: "none" | "solid" | "lines" | "cross" | "dots"; angle: number; spacing_mm: number; standard: null }
export interface ProjItem {
  id: string; category: string; name: string; depth: number; tris: number[][]; edges: number[][]; cut: Pt[][]; cut_material?: string[];
  /** A door or window's opening direction: two lines from the hinge edge, drawn solid when the leaf opens toward the viewer and dashed when away. */
  opening?: { kind: "door" | "window"; toward: boolean; lines: number[][]; /** the other swinging panels of a door, each solid or dashed on its own */ more?: Array<{ toward: boolean; lines: number[][] }> } | null;
  /** How the cut is hatched; absent when the view hides material hatching (the cut is then a plain fill) or the material has no assignment. */
  cut_hatch?: HatchSpec | null;
  /** A wall, floor or roof built in layers: which layer each loop of `cut` is, and (unless hatching is hidden) how each is hatched. */
  cut_layers?: Array<{ layer: string; material: string; name: string }>;
  cut_hatches?: Array<HatchSpec | null>;
}
export interface Projection {
  kind: "section" | "elevation"; items: ProjItem[]; levels: Array<{ name: string; z: number }>; bounds: [Pt, Pt] | null; span?: number; far?: number;
  /** The grids this picture meets: a vertical line at `x` (along the picture), with its bubble above. */
  grid_marks?: Array<{ id: string; name: string; x: number }>;
}
/** A section's cut line on a plan: its end points, which way it looks (a unit vector) and how far. */
export interface SectionMark { id: string; name: string; a: Pt; b: Pt; look: Pt; looking: string; depth: number }
export interface DrawingData {
  view: View; unit: string; revision: number; doc_revision: number; layers: Layer[]; entities: Entity[]; model: PlanWall[]; bounds: [Pt, Pt] | null;
  projection: Projection | null; section_marks: SectionMark[];
}

export interface ScheduleColumn { key: string; label: string; unit: string; total: boolean; align: "left" | "right" }
export interface ScheduleData {
  view?: View; revision?: number; category: string; title: string; columns: ScheduleColumn[]; rows: Array<Record<string, string | number>>;
  totals: Record<string, number>; count: number; note: string;
}

export interface SceneObject { id: string; name: string; category: string; material: string | null; vertices: Pt3[]; faces: number[][] }
export interface Scene { unit: "m"; revision: number; objects: SceneObject[]; bounds: [Pt3, Pt3] | null; time?: number }

/** The model's animation as the SLAI Animation Agent holds it in the SGEO scene. Times are seconds. ``lanes`` is the same tracks in the units
 *  the UI works in (metres, degrees, scale factors, visible / hidden). */
export type KeyValue = number | boolean | number[];
export interface AnimKeyframe { time: number; value: KeyValue }
export interface AnimTrack { object_id: string; property: string; keyframes: AnimKeyframe[]; interpolation?: string; axis?: number[] }
export interface AnimationSpec { duration: number; tracks: AnimTrack[] }
export type AnimProperty = "translation" | "turn" | "scale" | "visibility";
export type Interpolation = "smooth" | "linear" | "step";
export interface AnimLane { object_id: string; property: string; editable: boolean; interpolation: string; keys: AnimKeyframe[] }
export interface AnimMarker { id: string; name: string; time: number; object_id: string | null }
export interface AnimSettings { fps: number; loop: boolean; duration: number }
export interface AnimationData {
  agent: { available: boolean; reason?: string; hint?: string; operations?: string[]; unsupported?: string[] };
  animation: AnimationSpec | null; names: Record<string, string>; revision: number;
  lanes: AnimLane[]; markers: AnimMarker[]; settings: AnimSettings; properties: AnimProperty[]; interpolations: Interpolation[]; presets: Record<string, string>;
}
/** What one element looks like at one time, relative to the element as built, and which properties are keyed on that frame. */
export interface PoseData {
  id: string; name: string; time: number; keyed: AnimProperty[]; animated: AnimProperty[]; revision: number;
  pose: { translation: number[]; turn: number; scale: number[]; visibility: boolean };
}

export interface Choice { value: string; label: string }
/** Where a ``link`` row leads: select an element (or a drawing entity), open a view or sheet, or open a dialog. */
export interface LinkTarget { select?: { model?: string; drawing?: string; entity?: string }; open?: OpenRef; dialog?: string }
export interface PropRow {
  key?: string; label: string; type: "text" | "number" | "choice" | "readonly" | "link" | "sfb" | "catalog"; value: string | number; unit?: string; choices?: Choice[];
  /** A custom property or a note can be taken away. */
  removable?: boolean; target?: LinkTarget;
  /** An NL-SfB code: the table the picker searches, the name of the code that is set, and the codes usually given to this kind of element. */
  table?: number; text?: string; suggest?: SfbEntry[];
  /** A schema-backed IFC, OmniClass or UniFormat/Assembly Code picker. */
  catalog?: "ifc" | "omniclass" | "assembly"; catalog_suggest?: CatalogEntry[];
}
export interface SfbEntry { table: number; code: string; nl: string; en: string }
export interface CatalogEntry { code: string; title: string; table?: number; mapped?: string[]; predefined_types?: string[]; occurrence?: string }
export interface PropGroup {
  title: string; rows: PropRow[];
  /** Advanced classification groups start collapsed without hiding their data. */
  collapsed?: boolean;
  /** A button under the rows that opens a dialog (``issue``: issue a revision of the sheet). */
  action?: { label: string; dialog: string };
  /** A form to add a row: ``custom`` (a custom property) or ``note`` (a sheet note). */
  add?: "custom" | "note" | "layer" | "panel";
}
export interface Properties {
  ref: Record<string, string>; title: string; category: string; kind: "model" | "entity" | "sheet" | "view";
  top: PropRow[]; groups: PropGroup[];
}

/** What one conversion did, in the one shape every import and export uses: what was converted, lost, approximated and unsupported, and what failed. */
export interface ConversionReport {
  direction?: "import" | "export"; status?: "complete" | "partial" | "failed"; adapter?: string; accuracy_class?: string;
  source?: { format: string; name: string; bytes?: number; sha256?: string }; destination?: { format: string; target?: string };
  converted?: Record<string, number>; lost?: string[]; approximated?: string[]; unsupported?: string[]; errors?: string[]; warnings?: string[]; assumptions?: string[];
}
export interface FormatState { available: boolean; reason?: string | null; note?: string; native?: boolean; limits?: string[] }
export type ModelFormat = "sgeo" | "glb" | "stl" | "obj" | "ifc";
export type DrawingFormat = "sdrw" | "svg" | "dxf";
export interface Interop {
  importModel: Record<ModelFormat, FormatState>; importDrawing: Record<DrawingFormat, FormatState>;
  exportDrawing: Record<DrawingFormat | "dwg", FormatState>; exportModel: Record<Exclude<ModelFormat, "ifc">, FormatState>;
}
/** What the server found when it looked into a file without importing it. */
export interface InspectResult {
  target: "model" | "drawing"; format: string; detected?: string | null; name: string; bytes: number; sha256: string; valid: boolean; version: string | null;
  unit: { value: string; declared: boolean } | null; counts: Record<string, number | Record<string, number>>; issues: Array<{ severity: "error" | "warning" | "info"; message: string }>;
  extent?: { size: number[]; unit: string; metres: number }; report: ConversionReport | null; accuracy_class?: string; dependency_missing?: boolean;
}

/** A background operation. ``cancellable`` is false once its change is being applied to the project. */
export interface JobError { code: string; category: string; message: string; recovery: string }
export interface Job {
  id: string; kind: string; label: string; status: "queued" | "running" | "done" | "failed" | "cancelled"; progress: number; message: string;
  created: number; started: number | null; finished: number | null; cancellable: boolean; error: JobError | null; hasFile: boolean;
  file?: { name: string; bytes: number }; result?: Record<string, unknown>;
}
export interface DiagnosticEntry { at: string; source: string; code: string; category: string; message: string; project?: string; revision?: number; job?: string; trace?: string }

/** How the documentation stands against the model (``/api/sync``). */
export interface SyncStatus {
  model_revision: number; tags: number; stale_sheets: string[];
  sheets: Array<{ id: string; number: string; name: string; state: "not issued" | "current" | "changed"; revision: string; changed: string[] }>;
  detached_tags: Array<{ view: string; view_name: string; entity: string; element: string }>;
}
export interface FamilyParam { name: string; label: string; type: string; unit: string; required: boolean; default: number | null; min: number | null; max: number | null; expression: string | null; description: string }
export interface FamilyInfo { id: string; name: string; shape: string; instances: number; parameters: FamilyParam[] }
export interface FamiliesResponse { families: FamilyInfo[]; shapes: Record<string, string[]>; types: string[]; levels: Level[] }
export interface ReportView { report: { id: string; name: string; created: string; revision: number; sections: string[]; bytes: number; sha256: string; blocks: number }; markdown: string; container: Record<string, unknown> }
export interface CommandResult { revision: number; result: Record<string, unknown>; project: ProjectSummary }
export interface Issue { severity: "error" | "warning" | "info"; ref: string; message: string; category?: string }
export interface CheckResult { revision: number; issues: Issue[]; ok: boolean; scope: string; counts?: Record<string, number>; limitations?: string[] }

/** The 3D model as vector faces from a camera direction, far to near: ``[x1, y1, x2, y2, x3, y3, shade, edges]``, ``edges`` having one bit per side to draw. */
export interface SolidView { faces: number[][]; bounds: [Pt, Pt] | null }
export interface SnapshotImage { href: string; width_px: number; height_px: number; camera: { azimuth: number | null; elevation: number | null }; model_revision: number; taken: string }
export interface ReportLine { style: "h1" | "h2" | "h3" | "p" | "li" | "gap" | "head" | "row"; text?: string; cells?: [number, string][]; span?: number }
export interface ReportBox { name: string; lines: ReportLine[]; line_height: number; char_width: number; hidden: number; cut: boolean; revision: number }
export interface Viewport {
  id: string; /** Absent for a plan, section, elevation or schedule; ``model`` is the 3D model, ``snapshot`` a picture of the 3D view, ``report`` a report's text. */ kind?: "model" | "snapshot" | "report";
  view_id?: string; x: number; y: number; anchor: Pt; scale: string; title?: string;
  view: View | { name: string }; unit?: string; mm_per_unit?: number; layers?: Layer[]; entities?: Entity[]; model?: PlanWall[];
  projection?: Projection | null; section_marks?: SectionMark[]; schedule?: ScheduleData;
  azimuth?: number; elevation?: number; solid?: SolidView;
  image?: SnapshotImage; paper_width?: number; paper_height?: number; report?: ReportBox;
}
export interface SheetNote { id: string; text: string; x: number; y: number; size: number }
export interface SheetRevision { rev: string; date: string; description: string; by: string; model_revision: number }
export interface SheetData {
  id: string; number: string; name: string; paper: string; scale: string; revision: string; width: number; height: number; project: string;
  viewports: Viewport[]; revision_number: number;
  title_block?: Record<string, string | SheetRevision[]>; notes?: SheetNote[]; revisions?: SheetRevision[]; status?: { issued: boolean; state: string; revision: string; changed: string[] };
}

/** The SLAI STEM Agent's beam answer, with the inputs that were used. Units are SI as the agent reports them. */
export interface BeamAnalysis {
  id: string; name: string; agent: string; method: string; note: string; validation: string | null; revision: number;
  inputs: { span_m: number; width_m: number; depth_m: number; profile: string; section_source: string; area_m2: number; material: string; support: string; load_type: string; load: number; load_unit: string;
    youngs_modulus_gpa: number; youngs_modulus_source: string; second_moment_m4: number; section_modulus_m3: number };
  outputs: Record<string, { value: number; unit: string }>;
  assumptions: string[];
  details: { base_engineering_check?: { demand: number; limit: number; utilization: number; passed: boolean } };
}
export interface EngineeringInfo { available: boolean; reason?: string; hint?: string; supports: string[]; load_types: string[] }

/** Quantities of elements (``/api/quantities``): measured by SGEO, with density only from the SLAI engine or the person. */
export interface QuantityRow {
  id: string; name: string; category: string; material: string | null; volume_m3: number | null; surface_area_m2: number | null; size_m: number[] | null;
  density_kg_m3: number | null; density_source: string | null; mass_kg: number | null; centroid_m: number[] | null; notes: string[];
}
export interface Quantities {
  rows: QuantityRow[]; revision: number; note: string; source: string; needs_density: string[];
  totals: { count: number; measured: number; volume_m3: number; surface_area_m2: number; mass_kg: number | null; mass_complete: boolean };
  centre: { kind: "centre of mass" | "centre of volume"; point_m: number[]; complete: boolean } | null;
  by_material: Array<{ material: string; count: number; volume_m3: number; surface_area_m2: number; mass_kg: number | null; mass_complete: boolean }>;
}

/** What the project is (``/api/project/properties``): identity, counts, the files it saves with their recorded hashes, and its backups. */
export interface BackupInfo { id: string; created: string; name: string; revision: number; bytes: number }
export interface ProjectProperties {
  id: string; name: string; description: string; unit: string; format: string; created: string | null; modified: string | null; path: string | null;
  revision: number; dirty: boolean; model_unit: string; backups_kept: number; backups: BackupInfo[];
  counts: { levels: number; views: number; sheets: number; elements: Record<string, number> };
  files: Record<string, { sha256: string | null; revision: number | null; bytes: number | null }>;
}
