/** Export of the animation as pictures: a ZIP of PNG frames, or a WebM video. Both are made in the browser from what the server
 *  evaluates (the Animation Agent's scene at each frame time) and what the 3D view draws; the server renders nothing. The picture is the
 *  3D view as it is framed now, without selection or overlay. */
import type { Store } from "./store.js";
import type { Scene } from "./types.js";
import { zipStore, type ZipEntry } from "./zip.js";

export type ExportKind = "png" | "webm";
export const MAX_PNG_FRAMES = 3600;
/** A video is paced in real time from scenes held in memory; past this many vertices in total it is refused rather than run out of memory. */
export const MAX_VIDEO_VERTICES = 3_000_000;

export interface ExportOptions {
  store: Store;
  capture: (scene: Scene) => HTMLCanvasElement | null;
  kind: ExportKind;
  signal: AbortSignal;
  onProgress: (done: number, total: number, what: string) => void;
}
export interface ExportResult { blob: Blob; frames: number; fps: number; name: string; mime: string }

/** The times of the frames: every 1/fps from 0 to the end, the last frame exactly at the end. */
export function frameTimes(duration: number, fps: number): number[] {
  const n = Math.round(duration * fps);
  return Array.from({ length: n + 1 }, (_, i) => Math.round((i / fps) * 1e6) / 1e6);
}

export const frameName = (index: number, total: number): string => `frame_${String(index + 1).padStart(String(total).length < 4 ? 4 : String(total).length, "0")}.png`;

export function videoMime(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  return ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m)) ?? null;
}

export function exportUnavailable(kind: ExportKind, hasWebGl: boolean): string | null {
  if (!hasWebGl) return "The 3D view needs WebGL 2, which this browser does not provide.";
  if (kind === "webm" && !videoMime()) return "This browser cannot record WebM video. Export PNG frames instead.";
  return null;
}

const wait = (ms: number): Promise<void> => new Promise((r) => window.setTimeout(r, ms));

export async function exportAnimation(o: ExportOptions): Promise<ExportResult> {
  const { store } = o;
  const spec = store.animation?.animation;
  if (!spec) throw new Error("There is no animation to export. Set at least one key first.");
  const fps = store.animation?.settings.fps ?? 24;
  const times = frameTimes(spec.duration, fps);
  const project = (store.project?.name ?? "animation").replace(/[^\w.-]+/g, "_");
  const fetchScene = (t: number): Promise<Scene> => store.api.get<Scene>("/scene", { time: String(t) });
  const check = (): void => { if (o.signal.aborted) throw new DOMException("Export cancelled", "AbortError"); };

  if (o.kind === "png") {
    if (times.length > MAX_PNG_FRAMES) throw new Error(`${times.length} frames is more than ${MAX_PNG_FRAMES}. Lower the frame rate or the duration.`);
    const entries: ZipEntry[] = [];
    for (let i = 0; i < times.length; i++) {
      check();
      o.onProgress(i, times.length, "Rendering frames");
      const canvas = o.capture(await fetchScene(times[i] ?? 0));
      if (!canvas) throw new Error("The 3D view could not draw a frame.");
      const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/png"));
      if (!blob) throw new Error("The browser could not encode a frame as PNG.");
      entries.push({ name: frameName(i, times.length), data: new Uint8Array(await blob.arrayBuffer()) });
    }
    o.onProgress(times.length, times.length, "Packing");
    const zip = zipStore(entries);
    return { blob: new Blob([zip as BlobPart], { type: "application/zip" }), frames: times.length, fps, name: `${project}_frames.zip`, mime: "application/zip" };
  }

  const mime = videoMime();
  if (!mime) throw new Error("This browser cannot record WebM video. Export PNG frames instead.");
  const scenes: Scene[] = [];
  let vertices = 0;
  for (let i = 0; i < times.length; i++) {                     // fetch first, so recording is paced by the clock and not by the server
    check();
    o.onProgress(i, times.length, "Evaluating frames");
    const scene = await fetchScene(times[i] ?? 0);
    vertices += scene.objects.reduce((n, obj) => n + obj.vertices.length, 0);
    if (vertices > MAX_VIDEO_VERTICES) throw new Error("The model is too large to record as video in real time. Export PNG frames instead.");
    scenes.push(scene);
  }
  const first = o.capture(scenes[0] as Scene);
  if (!first) throw new Error("The 3D view could not draw a frame.");
  const target = document.createElement("canvas");
  target.width = first.width; target.height = first.height;
  const ctx = target.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw on a canvas.");
  const stream = target.captureStream(0);
  const track = stream.getVideoTracks()[0] as (MediaStreamTrack & { requestFrame?: () => void }) | undefined;
  if (!track?.requestFrame) throw new Error("This browser cannot capture canvas frames for video.");
  const chunks: Blob[] = [];
  const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8_000_000 });
  recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  const stopped = new Promise<void>((resolve, reject) => { recorder.onstop = () => resolve(); recorder.onerror = () => reject(new Error("The browser's video recorder failed.")); });
  recorder.start();
  const begin = performance.now();
  try {
    for (let i = 0; i < scenes.length; i++) {
      check();
      o.onProgress(i, scenes.length, "Recording");
      const canvas = o.capture(scenes[i] as Scene);
      if (!canvas) throw new Error("The 3D view could not draw a frame.");
      ctx.drawImage(canvas, 0, 0);
      track.requestFrame();
      await wait(Math.max(0, begin + ((i + 1) * 1000) / fps - performance.now()));
    }
  } finally {
    if (recorder.state !== "inactive") recorder.stop();
  }
  await stopped;
  const blob = new Blob(chunks, { type: "video/webm" });
  if (blob.size === 0) throw new Error("The recorder produced no video.");
  return { blob, frames: scenes.length, fps, name: `${project}.webm`, mime };
}
