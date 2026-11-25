/** The 3D snapshot dialog: size, quality, projection, background and overlays, then a progress dialog with Cancel while the picture is drawn offscreen.
 *  Nothing here touches the view or the project: the picture is drawn from a copy of what the view shows and saved as a file. */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog, saveBlob, toast } from "../../../tools/web/src/ui.js";
import { VisualControls } from "./visual_ui.js";
import { updatedVisual } from "./visual_shading.js";
import { ASPECTS, MAX_PIXELS, qualityDefaults, renderSnapshot, RESOLUTIONS, sizeFor, snapshotFilename, SnapshotCancelled, SnapshotUnavailable, defaultSettings, } from "./snapshot_render.js";
const KEY = "spadra.snapshot.settings";
function load() {
    try {
        const raw = localStorage.getItem(KEY);
        return raw ? JSON.parse(raw) : {};
    }
    catch {
        return {};
    }
}
/** The mode picked in the dialog is for one picture: it is never kept with the other choices, and the view's own settings are never written from here. */
function save(c) { try {
    const { visual: _temporary, ...settings } = c.settings;
    localStorage.setItem(KEY, JSON.stringify({ ...c, settings }));
}
catch { /* private mode: the choices simply are not kept */ } }
/** The picture's width and height for the current choices (the view's own aspect where asked). */
export function sizeOf(c, viewAspect) {
    if (c.resolution === "custom")
        return { width: Math.round(c.customW), height: Math.round(c.customH) };
    const edge = RESOLUTIONS.find((r) => r.id === c.resolution)?.edge ?? 1920;
    const preset = ASPECTS.find((a) => a.id === c.aspect);
    const ratio = c.aspect === "custom" ? c.customRatioW / Math.max(0.0001, c.customRatioH) : preset?.ratio ?? viewAspect;
    return sizeFor(edge, ratio);
}
export async function snapshotDialog(read, viewAspect, projectName) {
    const input = read();
    if (!input) {
        toast("The 3D view could not draw a picture. It needs WebGL 2, which this browser does not provide.", { kind: "error" });
        return;
    }
    if (input.items.length === 0) {
        toast("There is nothing in the 3D view to take a picture of yet.", { kind: "info" });
        return;
    }
    const saved = load();
    const c = { resolution: "1080p", aspect: "view", customW: 1920, customH: 1080, customRatioW: 16, customRatioH: 9, settings: defaultSettings(1920, 1080), ...saved };
    delete c.settings.visual; // a saved choice never carries a mode: the picture starts from the view's own
    c.settings = { ...defaultSettings(1920, 1080), ...(saved.settings ?? {}), overlays: { ...defaultSettings(1920, 1080).overlays, ...(saved.settings?.overlays ?? {}) },
        background: { ...defaultSettings(1920, 1080).background, ...(saved.settings?.background ?? {}) } };
    const select = (label, options, value, onChange) => {
        const el = h("select", { attrs: { "aria-label": label } }, ...options.map(([v, t]) => h("option", { value: v, text: t })));
        el.value = value;
        el.addEventListener("change", () => { onChange(el.value); refresh(); });
        return { row: h("label", { class: "field" }, h("span", { text: label }), el), el };
    };
    const num = (label, value, min, max, onChange, step = 1) => {
        const el = h("input", { type: "number", value: String(value), min: String(min), max: String(max), step: String(step), attrs: { "aria-label": label } });
        el.addEventListener("input", () => { const n = Number(el.value); if (Number.isFinite(n)) {
            onChange(n);
            refresh();
        } });
        return { row: h("label", { class: "field" }, h("span", { text: label }), el), el };
    };
    const tick = (label, value, onChange) => {
        const el = h("input", { type: "checkbox", checked: value, attrs: { "aria-label": label } });
        el.addEventListener("change", () => { onChange(el.checked); refresh(); });
        return { row: h("label", { class: "check" }, el, h("span", { text: label })), el };
    };
    const s = c.settings;
    const resolution = select("Resolution", [...RESOLUTIONS.map((r) => [r.id, r.label]), ["custom", "Custom size…"]], c.resolution, (v) => { c.resolution = v; });
    const aspect = select("Aspect ratio", [...ASPECTS.map((a) => [a.id, a.label]), ["custom", "Custom ratio…"]], c.aspect, (v) => { c.aspect = v; });
    const customSize = h("div", { class: "row" }, num("Width (px)", c.customW, 16, 16384, (v) => { c.customW = v; }).row, num("Height (px)", c.customH, 16, 16384, (v) => { c.customH = v; }).row);
    const customRatio = h("div", { class: "row" }, num("Ratio width", c.customRatioW, 0.1, 100, (v) => { c.customRatioW = v; }, 0.1).row, num("Ratio height", c.customRatioH, 0.1, 100, (v) => { c.customRatioH = v; }, 0.1).row);
    const quality = select("Quality", [["draft", "Draft (fast: no occlusion)"], ["standard", "Standard"], ["high", "High"], ["maximum", "Maximum"]], s.quality, (v) => {
        s.quality = v;
        Object.assign(s, qualityDefaults(s.quality));
        aa.el.checked = s.antialias;
        ao.el.checked = s.occlusion;
        tm.el.checked = s.toneMapping;
    });
    const aa = tick("Anti-aliasing (smooth edges)", s.antialias, (v) => { s.antialias = v; });
    const ao = tick("Ambient occlusion (soft shading in corners; Realistic only)", s.occlusion, (v) => { s.occlusion = v; });
    const tm = tick("Tone mapping (filmic contrast; Realistic only)", s.toneMapping, (v) => { s.toneMapping = v; });
    const projection = select("Projection", [["view", "As in the view"], ["orthographic", "Orthographic"], ["perspective", "Perspective"]], s.projection, (v) => { s.projection = v; });
    const framing = select("Framing", [["view", "As in the view"], ["fit", "Fit the whole model"]], s.framing, (v) => { s.framing = v; });
    const colour = h("input", { type: "color", value: s.background.colour, attrs: { "aria-label": "Background colour" } });
    colour.addEventListener("input", () => { s.background.colour = colour.value; });
    const background = select("Background", [["paper", "Paper (the view's)"], ["white", "White"], ["colour", "Colour…"], ["transparent", "Transparent (PNG only)"]], s.background.kind, (v) => { s.background.kind = v; });
    const edges = tick("Add edge lines to Realistic (other modes have their own)", s.overlays.edges, (v) => { s.overlays.edges = v; });
    const grid = tick("Ground grid", s.overlays.grid, (v) => { s.overlays.grid = v; });
    const axes = tick("World axes", s.overlays.axes, (v) => { s.overlays.axes = v; });
    const format = select("File type", [["png", "PNG (lossless)"], ["jpeg", "JPEG (smaller)"]], s.format, (v) => { s.format = v; });
    const jpegQ = num("JPEG quality (%)", Math.round(s.jpegQuality * 100), 40, 100, (v) => { s.jpegQuality = v / 100; });
    let visual = structuredClone(input.visual), changed = false;
    const look = new VisualControls({ get: () => visual, commit: (patch) => { visual = updatedVisual(visual, patch); changed = true; look.update(); } }, { label: "Display mode for this picture" });
    const readout = h("p", { class: "muted", attrs: { "aria-live": "polite" } });
    const problem = h("p", { class: "field-error", attrs: { role: "alert" } });
    const refresh = () => {
        const size = sizeOf(c, viewAspect);
        s.width = size.width;
        s.height = size.height;
        customSize.hidden = c.resolution !== "custom";
        aspect.row.hidden = c.resolution === "custom";
        customRatio.hidden = c.resolution === "custom" || c.aspect !== "custom";
        colour.hidden = s.background.kind !== "colour";
        jpegQ.row.hidden = s.format !== "jpeg";
        const pixels = s.width * s.height;
        let msg = "";
        if (!Number.isFinite(pixels) || s.width < 16 || s.height < 16)
            msg = "The picture must be at least 16 × 16 pixels.";
        else if (pixels > MAX_PIXELS)
            msg = `That is ${(pixels / 1e6).toFixed(1)} million pixels; the most is ${MAX_PIXELS / 1e6} million.`;
        else if (s.background.kind === "transparent" && s.format === "jpeg")
            msg = "JPEG cannot be transparent: the background will be white. Choose PNG to keep it transparent.";
        readout.textContent = `${s.width} × ${s.height} px · ${(pixels / 1e6).toFixed(1)} megapixels`;
        problem.textContent = msg;
        problem.hidden = !msg;
    };
    refresh();
    const body = h("div", { class: "snapshot-form" }, h("fieldset", {}, h("legend", { text: "Size" }), resolution.row, aspect.row, customSize, customRatio, readout), h("fieldset", {}, h("legend", { text: "Look (this picture only)" }), look.el, h("p", { class: "muted", text: "Starts as the 3D view is drawn. Changing it here does not change the view." })), h("fieldset", {}, h("legend", { text: "Quality" }), quality.row, aa.row, ao.row, tm.row), h("fieldset", {}, h("legend", { text: "View" }), projection.row, framing.row), h("fieldset", {}, h("legend", { text: "Background and overlays" }), background.row, colour, edges.row, grid.row, axes.row), h("fieldset", {}, h("legend", { text: "File" }), format.row, jpegQ.row), problem);
    const dialog = openDialog({
        title: "3D snapshot", eyebrow: "Picture of this view", size: "wide", body, noCancel: false, cancelLabel: "Close",
        actions: [{ label: "Save picture…", kind: "primary", run: () => {
                    refresh();
                    if (problem.textContent && !problem.textContent.startsWith("JPEG"))
                        return undefined;
                    save(c);
                    return true;
                } }],
    });
    if (!(await dialog.result))
        return;
    await runWithProgress(read() ?? input, { ...s, overlays: { ...s.overlays }, background: { ...s.background }, ...(changed ? { visual } : {}) }, projectName); // unchanged: the view's own, read just now
}
/** Draw with a progress dialog that can cancel, then hand the file to the browser's download. */
export async function runWithProgress(input, s, projectName) {
    const control = new AbortController();
    const bar = h("progress", { max: "100", value: "0", attrs: { "aria-label": "Snapshot progress" } });
    const label = h("p", { class: "muted", text: "Starting…", attrs: { "aria-live": "polite" } });
    const dialog = openDialog({ title: "Drawing the snapshot", size: "narrow", body: h("div", {}, bar, label), cancelLabel: "Cancel" });
    void dialog.result.then((value) => { if (value === null)
        control.abort(); });
    try {
        const blob = await renderSnapshot(input, s, { signal: control.signal, onProgress: (f, text) => { bar.value = Math.round(f * 100); label.textContent = text; } });
        dialog.close(true);
        const name = snapshotFilename(projectName, s);
        saveBlob(blob, name);
        toast(`Saved ${name} (${(blob.size / 1024 / 1024).toFixed(1)} MB).`, { kind: "ok", timeoutMs: 3500 });
        return true;
    }
    catch (error) {
        dialog.close(null);
        if (error instanceof SnapshotCancelled) {
            toast("Snapshot cancelled. Nothing was saved.", { kind: "info" });
            return false;
        }
        toast(error instanceof SnapshotUnavailable || error instanceof Error ? error.message : "The snapshot could not be drawn.", { kind: "error" });
        return false;
    }
}
