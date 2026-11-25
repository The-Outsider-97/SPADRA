/** Profiler's wire types and the thin calls to its HTTP API (``/profiler``). Nothing here decides anything: the server owns every rule. */
import type { Client } from "../../../tools/web/src/api.js";

export type Pt = [number, number];

export interface PfEdge { id: string; type: string; layer: string; color: string; construction: boolean; locked: boolean; points: Pt[]; center?: Pt; radius?: number; ends?: string[] }
export interface PfVertex { id: string; x: number; y: number }
export interface PfDimGeometry { kind: string; ext?: Pt[][]; line?: Pt[]; arc?: Pt[]; label: Pt }
export interface PfDim { id: string; type: string; mode: string; state: string; refs: string[]; param: string | null; expr: string | null; anchor: string; name: string | null; value: number;
  text: string; geometry: PfDimGeometry; unresolved?: boolean; error?: string; style: { color: string; text_height: number } }
export interface PfConstraint { id: string; kind: string; refs: string[]; state: string; enabled: boolean; origin: string; value: number | null; at: Pt }
export interface PfComment { id: string; at: Pt; text: string; author: string; created: string; resolved: boolean; replies: Array<{ author: string; text: string; created: string }>; anchor: { kind: string } }
export interface PfText { id: string; xy: Pt; text: string; height: number; rotation: number; color: string | null; layer: string }
export interface PfHatch { id: string; pattern: string; color: string; rings: Pt[][]; segments: Array<[Pt, Pt]> }
export interface PfLayer { name: string; visible: boolean; locked: boolean; color: string | null }
export interface PfGroup { id: string; kind: string; name?: string; ids?: string[]; members?: string[] }
export interface PfParamRow { id: string; name: string; type: string; scope: string; unit: string; value: number | boolean | string; expression: string | null; derived: boolean; read_only: boolean;
  min: number | null; max: number | null; description: string; depends_on: string[]; used_by: string[]; dimensions: Array<{ dimension: string; type: string }>; valid: boolean; error: string | null }
export interface PfRender {
  revision: number; name: string; unit: string; precision: number; default_color: string; bounds: [number, number, number, number] | null;
  view: { grid: number; snap_grid: boolean; rulers: boolean; axes: boolean; origin: Pt };
  layers: PfLayer[]; edges: PfEdge[]; vertices: PfVertex[]; dimensions: PfDim[]; constraints: PfConstraint[]; comments: PfComment[]; texts: PfText[]; hatches: PfHatch[];
  groups: PfGroup[]; params: Record<string, unknown>; chains: Record<string, unknown>; features: unknown[];
}
export interface PfStatus { id: string; name: string; revision: number; dirty: boolean; path: string | null; can_undo: boolean; can_redo: boolean; undo_label: string | null; redo_label: string | null;
  unit: string; entities: { vertices: number; edges: number }; can_repeat: boolean; clipboard: boolean }
export interface PfDiagnostics { status: "under" | "full" | "conflicting" | "invalid"; ok: boolean; dof: number; converged: boolean; conflicting?: string[]; redundant?: string[]; message?: string; [k: string]: unknown }
export interface PfView { status: PfStatus; render: PfRender; params: { parameters: PfParamRow[]; variants?: string[]; blocks?: string[]; components?: string[]; styles?: string[]; [k: string]: unknown }; diagnostics: PfDiagnostics }
export interface PfOpResult { result: unknown; changed: boolean; revision: number; status: PfStatus; render?: PfRender; params?: PfView["params"]; diagnostics?: PfDiagnostics }
export interface PfIssue { id: string; kind: string; severity: "error" | "warning" | "info"; message: string; refs: string[]; point?: Pt; repair?: string | null; edges?: string[] }
export interface PfCheckSummary { edges?: number; regions?: number; open_ends?: number; area?: number; dof?: number; solver?: string }
export interface PfCheck { purpose: string; status: string; suitable: boolean; counts: Record<string, number>; issues: PfIssue[]; summary: PfCheckSummary | string }
export interface PfSnap { snap: { mode: string; point: Pt; distance: number; ref?: string; edge?: string } | null; candidates: Array<{ mode: string; point: Pt }> }
export interface PfRegion { outer: { edges: string[]; points: Pt[]; area: number; perimeter: number }; holes: Array<{ edges: string[]; points: Pt[] }>; area: number; perimeter: number; edges: string[] }
export interface PfInfo { templates: Array<{ id: string; label: string }>; units: string[]; operations: string[]; kinds: string[]; modes: string[]; open: PfStatus[]; profiles: Array<{ name: string; path: string; modified: number; size: number }>;
  folders: { profiles: string; autosave: string }; recoverable: Array<{ path: string; name?: string; origin_path?: string | null; saved_at?: string; damaged?: boolean; reason?: string; newer_than_original?: boolean }>;
  project: { open: boolean; unit?: string; name?: string; levels?: Array<{ id: string; name: string }> } }
export interface PluginInfo { ribbon: string; id: string; label: string; state: "ready" | "separate" | "unavailable" | "missing"; detail?: string; reason?: string; notes?: string[]; run?: string; opens?: string }

export class ProfilerApi {
  constructor(private readonly api: Client) {}
  plugins(): Promise<{ plugins: PluginInfo[]; ribbons: Record<string, string[]> }> { return this.api.get("/plugins"); }
  info(): Promise<PfInfo> { return this.api.get("/profiler"); }
  create(body: { template: string; name: string; unit: string; values?: Record<string, number> }): Promise<PfView> { return this.api.post("/profiler/new", body); }
  openUpload(file: File): Promise<PfView & { report: { warnings: string[]; notes: string[]; kind: string } }> { return this.api.upload(`/profiler/import?name=${encodeURIComponent(file.name)}`, file); }
  open(body: { path?: string; recover?: string; project_object?: string }): Promise<PfView & { report: { warnings: string[]; notes: string[]; kind: string } }> { return this.api.post("/profiler/open", body); }
  state(sid: string): Promise<PfView> { return this.api.get(`/profiler/${sid}`); }
  op<T = unknown>(sid: string, op: string, args: Record<string, unknown> = {}, view?: boolean): Promise<PfOpResult & { result: T }> {
    return this.api.post(`/profiler/${sid}/op`, { op, args, ...(view === undefined ? {} : { view }) });
  }
  undo(sid: string): Promise<PfView & { undone: string }> { return this.api.post(`/profiler/${sid}/undo`); }
  redo(sid: string): Promise<PfView & { redone: string }> { return this.api.post(`/profiler/${sid}/redo`); }
  repeat(sid: string): Promise<PfOpResult> { return this.api.post(`/profiler/${sid}/repeat`, {}); }
  save(sid: string, body: { path?: string; overwrite?: boolean; force?: boolean }): Promise<{ path: string; status: PfStatus }> { return this.api.post(`/profiler/${sid}/save`, body); }
  autosave(sid: string): Promise<{ autosaved: { path: string } | null; status: PfStatus }> { return this.api.post(`/profiler/${sid}/autosave`); }
  importFile(sid: string, body: { path: string; insert?: boolean; unit?: string }): Promise<PfView & { imported: Record<string, unknown> }> { return this.api.post(`/profiler/${sid}/import`, body); }
  importUpload(sid: string, file: File, insert: boolean): Promise<PfView & { imported: Record<string, unknown> }> { return this.api.upload(`/profiler/${sid}/import_upload?name=${encodeURIComponent(file.name)}&insert=${insert ? 1 : 0}`, file); }
  exportFile(sid: string, body: { format: string; path: string; overwrite?: boolean }): Promise<{ path: string }> { return this.api.post(`/profiler/${sid}/export`, body); }
  report(sid: string, body: { path: string; format: string; purpose: string; overwrite?: boolean }): Promise<{ path: string }> { return this.api.post(`/profiler/${sid}/report`, body); }
  check(sid: string, purpose: string, extra: Record<string, unknown> = {}): Promise<PfCheck> { return this.api.post(`/profiler/${sid}/check`, { purpose, ...extra }); }
  send(sid: string, body: { kind: string; options: Record<string, unknown>; close?: boolean }): Promise<{ result: { ids: string[]; id: string; warnings: string[] }; closed: boolean; close_blocked?: string; warnings: string[] }> {
    return this.api.post(`/profiler/${sid}/send`, body);
  }
  close(sid: string, discard: boolean): Promise<{ closed: string }> { return this.api.post(`/profiler/${sid}/close`, { discard }); }
}
