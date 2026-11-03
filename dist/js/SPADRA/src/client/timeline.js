/** The animation panel under the 3D view.
 *
 *  How a person animates here: select an element, move the playhead to a time, change its Pose (or drag it in the 3D view with "Drag in 3D
 *  view" on). A key is made for exactly what was changed (Auto-key). The panel keeps no animation of its own: every change is a project
 *  command that the server carries out through the SLAI Animation Agent, and the picture at any time is the agent's evaluation. */
import { h, replace } from "../../../tools/web/src/dom.js";
import { saveBlob, toast } from "../../../tools/web/src/ui.js";
import { exportAnimation, exportUnavailable } from "./animation_export.js";
export const PROPERTY_LABEL = { translation: "Move", turn: "Turn", scale: "Scale", visibility: "Show / hide" };
const ORDER = ["translation", "turn", "scale", "visibility"];
const INTERPOLATION_LABEL = { smooth: "Smooth", linear: "Linear", step: "Step" };
const SPEEDS = [0.25, 0.5, 1, 2];
const FPS_CHOICES = [12, 24, 25, 30, 48, 60];
const ZOOMS = [1, 2, 4, 8];
const fmt = (n) => (Math.round(n * 100) / 100).toFixed(2);
export const laneLabel = (property) => PROPERTY_LABEL[property] ?? property.replace(/^parameter\./, "");
/** What a key's value reads as: a move in metres, a turn in degrees, scale factors, or shown / hidden. */
export function describeValue(property, value) {
    if (property === "translation" && Array.isArray(value))
        return `${value.map((v) => fmt(v)).join(", ")} m`;
    if (property === "scale" && Array.isArray(value))
        return `×${value.map((v) => fmt(v)).join(", ×")}`;
    if (property === "turn" && typeof value === "number")
        return `${fmt(value)}°`;
    if (property === "visibility")
        return value ? "shown" : "hidden";
    return String(value);
}
/** "m:ss.ff" for the readout: seconds with the frame number, so a person can see which frame they are on. */
export function formatTime(seconds, fps) {
    const frame = Math.round(seconds * fps);
    return `${fmt(seconds)} s · frame ${frame}`;
}
/** Distance between ruler labels, in seconds: the first of a few round steps that leaves at least ``minPx`` between labels. */
export function rulerStep(duration, widthPx, minPx = 56) {
    const steps = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const pxPerSecond = widthPx / Math.max(duration, 1e-9);
    return steps.find((s) => s * pxPerSecond >= minPx) ?? steps[steps.length - 1] ?? 60;
}
/** The time under a pointer at ``x`` inside a strip ``width`` wide covering 0..duration, snapped to a whole frame. */
export function timeFromX(x, width, duration, fps) {
    if (width <= 0 || duration <= 0)
        return 0;
    const t = Math.min(Math.max(x / width, 0), 1) * duration;
    return Math.min(duration, Math.round(Math.round(t * fps) / fps * 1e6) / 1e6);
}
/** Lanes grouped by element, in the order the elements first appear, and within an element in the fixed property order. */
export function groupLanes(lanes) {
    const groups = [];
    for (const lane of lanes) {
        let g = groups.find((x) => x.object_id === lane.object_id);
        if (!g) {
            g = { object_id: lane.object_id, lanes: [] };
            groups.push(g);
        }
        g.lanes.push(lane);
    }
    for (const g of groups)
        g.lanes.sort((a, b) => (ORDER.indexOf(a.property) + 1 || 99) - (ORDER.indexOf(b.property) + 1 || 99));
    return groups;
}
export class Timeline {
    store;
    capture;
    webgl;
    el;
    signature = "";
    readout = null;
    scrub = null;
    playButton = null;
    raf = 0;
    last = 0;
    waiting = false;
    speed = 1;
    zoom = 1;
    autoKey = true;
    presetsOpen = false;
    collapsed = false;
    selectedKey = null;
    exporter = null;
    exportKind = "png";
    preset = { kind: "slide", by: ["0", "0", "1"], degrees: "360", factor: "2", start: "", end: "" };
    constructor(store, capture = () => null, webgl = () => true) {
        this.store = store;
        this.capture = capture;
        this.webgl = webgl;
        this.el = h("section", { class: "timeline", hidden: true, attrs: { "aria-label": "Animation", tabindex: "-1" } });
        this.el.addEventListener("keydown", (e) => this.key(e));
    }
    // ------------------------------------------------------------------------------------------------------------------- rendering
    /** Called on every store change. Rebuilds only when something other than the playhead changed. */
    render() {
        const show = this.store.tab?.ref.kind === "3d" && !!this.store.project && !!this.store.boot?.capabilities.animation;
        this.el.hidden = !show;
        if (!show) {
            this.stop();
            return;
        }
        const a = this.store.animation;
        const sel = this.store.selection?.kind === "model" ? this.store.selection.ids : [];
        const signature = JSON.stringify([a?.revision, a?.agent.available, a?.settings, a?.animation?.duration, sel, this.zoom, this.autoKey, this.collapsed, this.selectedKey, this.store.dragToKey,
            this.store.pose ? [this.store.pose.id, this.store.pose.time, this.store.pose.revision, this.store.pose.keyed] : null, this.exporter !== null, this.exportKind]);
        if (signature !== this.signature) {
            this.signature = signature;
            this.build();
        }
        this.syncTime();
    }
    /** Move the playhead and readout to the store's time without rebuilding anything. */
    syncTime() {
        const t = this.store.animTime, d = this.duration(), fps = this.store.animation?.settings.fps ?? 24;
        if (this.scrub && document.activeElement !== this.scrub)
            this.scrub.value = String(t);
        if (this.readout)
            this.readout.textContent = `${formatTime(t, fps)} / ${fmt(d)} s`;
        this.el.querySelectorAll(".playhead").forEach((p) => p.style.setProperty("left", `${d > 0 ? (t / d) * 100 : 0}%`));
        const pose = this.store.pose;
        if (pose && Math.abs(pose.time - t) > 1e-9)
            this.el.querySelector(".tl-pose")?.classList.add("stale"); // the pose shown is for an earlier time until it loads
        else
            this.el.querySelector(".tl-pose")?.classList.remove("stale");
    }
    duration() { return this.store.animation?.animation?.duration ?? this.store.animation?.settings.duration ?? 0; }
    fps() { return this.store.animation?.settings.fps ?? 24; }
    selectedId() {
        const s = this.store.selection;
        return s?.kind === "model" && s.ids.length === 1 ? s.ids[0] : undefined;
    }
    build() {
        const a = this.store.animation;
        if (!a) {
            replace(this.el);
            this.scrub = null;
            this.readout = null;
            return;
        }
        if (!a.agent.available) {
            replace(this.el, h("div", { class: "tl-bar" }, h("strong", { text: "Animation" }), h("span", { class: "muted", text: `Unavailable: ${a.agent.reason ?? "the SLAI Animation Agent could not be loaded"}. ${a.agent.hint ?? ""}` })));
            return;
        }
        replace(this.el, this.bar(), this.collapsed ? null : h("div", { class: "tl-body" }, this.lanesView(), this.posePanel()));
    }
    // ------------------------------------------------------------------------------------------------------------------- transport
    button(label, text, onClick, extra = {}) {
        return h("button", { type: "button", class: "tool-inline", attrs: { "aria-label": label, title: label, ...extra }, on: { click: onClick }, text });
    }
    bar() {
        const a = this.store.animation;
        const d = this.duration();
        const playing = this.store.playing;
        const scrub = h("input", { type: "range", class: "tl-scrub", attrs: { min: "0", max: String(d), step: String(1 / this.fps()), "aria-label": "Time" } });
        scrub.value = String(this.store.animTime);
        scrub.addEventListener("input", () => { this.stop(); void this.store.seek(Number(scrub.value)); });
        const readout = h("span", { class: "tl-time", attrs: { "aria-live": "off" } });
        const play = h("button", { type: "button", id: "anim-play", class: "tool-inline primary", attrs: { "aria-label": playing ? "Pause" : "Play", title: "Play / pause (Space)" }, on: { click: () => this.toggle() }, text: playing ? "Pause" : "Play" });
        this.scrub = scrub;
        this.readout = readout;
        this.playButton = play;
        const select = (id, label, choices, value, on) => {
            const el = h("select", { id, attrs: { "aria-label": label } }, choices.map(([v, t]) => h("option", { value: v, text: t, selected: v === value })));
            el.addEventListener("change", () => on(el.value));
            return h("label", { class: "tl-field" }, label, el);
        };
        const number = (id, label, value, min, step, on) => {
            const el = h("input", { type: "number", id, attrs: { min, step, "aria-label": label } });
            el.value = String(value);
            el.addEventListener("change", () => { if (Number.isFinite(el.valueAsNumber))
                on(el.valueAsNumber); });
            return h("label", { class: "tl-field" }, label, el, "s");
        };
        const loop = h("input", { type: "checkbox", id: "anim-loop", attrs: { "aria-label": "Loop" } });
        loop.checked = a.settings.loop;
        loop.addEventListener("change", () => { void this.store.animate("settings", { loop: loop.checked }); });
        const exporting = this.exporter !== null;
        const unavailable = exportUnavailable(this.exportKind, this.webgl());
        const exportButton = h("button", { type: "button", id: "anim-export", class: "tool-inline", on: { click: () => { void this.export(); } }, text: exporting ? "Cancel export" : "Export",
            attrs: { title: unavailable ?? "Export the animation as pictures, framed as the 3D view is now" } });
        exportButton.disabled = !exporting && (!!unavailable || !a.animation);
        if (!a.animation && !exporting)
            exportButton.title = "Set at least one key first";
        const fold = this.button(this.collapsed ? "Show the lanes and Pose panel" : "Hide the lanes and Pose panel", this.collapsed ? "▸" : "▾", () => { this.collapsed = !this.collapsed; this.signature = ""; this.render(); }, { id: "anim-fold", "aria-expanded": String(!this.collapsed) });
        return h("div", { class: "tl-bar" }, fold, this.button("Go to start", "⏮", () => { this.stop(); void this.store.seek(0); }, { id: "anim-start" }), this.button("Previous frame (←)", "◀", () => this.step(-1), { id: "anim-prev" }), play, this.button("Next frame (→)", "▶", () => this.step(1), { id: "anim-next" }), this.button("Go to end", "⏭", () => { this.stop(); void this.store.seek(d); }, { id: "anim-end" }), scrub, readout, h("label", { class: "tl-field" }, loop, "Loop"), select("anim-speed", "Speed", SPEEDS.map((s) => [String(s), `${s}×`]), String(this.speed), (v) => { this.speed = Number(v); }), select("anim-fps", "Frames/s", FPS_CHOICES.concat(FPS_CHOICES.includes(a.settings.fps) ? [] : [a.settings.fps]).sort((x, y) => x - y).map((f) => [String(f), String(f)]), String(a.settings.fps), (v) => { this.stop(); void this.store.animate("settings", { fps: Number(v) }); }), number("anim-duration", "Length", d, "0.1", "0.5", (v) => { void this.store.animate("settings", { duration: v }); }), select("anim-zoom", "Zoom", ZOOMS.map((z) => [String(z), `${z}×`]), String(this.zoom), (v) => { this.zoom = Number(v); this.signature = ""; this.render(); }), select("anim-export-kind", "Export as", [["png", "PNG frames (ZIP)"], ["webm", "Video (WebM)"]], this.exportKind, (v) => { this.exportKind = v; this.signature = ""; this.render(); }), exportButton, h("span", { id: "anim-export-progress", class: "muted", attrs: { role: "status", "aria-live": "polite" } }), this.button("Remove the whole animation", "Clear", () => { void this.clear(); }, { id: "anim-clear" }));
    }
    async clear() {
        if (!this.store.animation?.animation) {
            this.store.setStatus("There is no animation to clear");
            return;
        }
        this.stop();
        if (await this.store.editAnimation("clear_animation", {}))
            await this.store.seek(0);
    }
    step(frames) {
        this.stop();
        void this.store.seek(this.store.animTime + frames * this.store.frameLength);
    }
    // ------------------------------------------------------------------------------------------------------------------- lanes
    lanesView() {
        const a = this.store.animation;
        const d = this.duration();
        const groups = groupLanes(a.lanes);
        const names = [h("div", { class: "tl-row tl-corner", text: "" })];
        const strips = [this.ruler(d, a.markers)];
        if (!groups.length) {
            names.push(h("div", { class: "tl-row tl-empty-name" }));
            strips.push(h("div", { class: "tl-row tl-empty" }, h("span", { class: "muted", text: "No keys yet. Select an element, then change its Pose on the right." }), h("div", { class: "playhead" })));
        }
        for (const g of groups) {
            const label = a.names[g.object_id] ?? "(deleted element)";
            names.push(h("div", { class: "tl-row tl-group", data: { object: g.object_id }, text: label }));
            strips.push(h("div", { class: "tl-row tl-group-strip" }, h("div", { class: "playhead" })));
            for (const lane of g.lanes) {
                names.push(this.laneHead(lane, label));
                strips.push(this.laneStrip(lane, d));
            }
        }
        const canvas = h("div", { class: "tl-canvas" }, ...strips);
        canvas.style.setProperty("width", `${this.zoom * 100}%`);
        return h("div", { class: "tl-lanes" }, h("div", { class: "tl-names" }, ...names), h("div", { class: "tl-scroll" }, canvas));
    }
    ruler(d, markers) {
        const ruler = h("div", { class: "tl-row tl-ruler", attrs: { role: "slider", "aria-label": "Playhead (drag)", "aria-valuemin": 0, "aria-valuemax": d, "aria-valuenow": this.store.animTime, tabindex: "0" } });
        const width = Math.max(300, (this.el.getBoundingClientRect().width || 900) * 0.55 * this.zoom);
        const step = rulerStep(d, width);
        for (let t = 0; t <= d + 1e-9; t += step) {
            const tick = h("span", { class: "tl-tick", text: step < 1 ? t.toFixed(2) : String(Math.round(t * 100) / 100) });
            tick.style.setProperty("left", `${d > 0 ? (t / d) * 100 : 0}%`);
            ruler.append(tick);
        }
        for (const m of markers) {
            const flag = h("button", { type: "button", class: "tl-marker", attrs: { title: `${m.name} at ${fmt(m.time)} s (click to go there, double-click to delete)`, "aria-label": `Marker ${m.name} at ${fmt(m.time)} seconds` }, data: { marker: m.id }, text: "⚑" });
            flag.style.setProperty("left", `${d > 0 ? (m.time / d) * 100 : 0}%`);
            flag.addEventListener("pointerdown", (e) => e.stopPropagation());
            flag.addEventListener("click", (e) => { e.stopPropagation(); this.stop(); void this.store.seek(m.time); });
            flag.addEventListener("dblclick", (e) => { e.stopPropagation(); void this.store.animate("delete_marker", { marker: m.id }); });
            ruler.append(flag);
        }
        ruler.append(h("div", { class: "playhead" }));
        this.dragPlayhead(ruler, d);
        return ruler;
    }
    /** Pressing or dragging on a strip puts the playhead there (snapped to a frame). */
    dragPlayhead(strip, d) {
        const to = (e) => {
            const r = strip.getBoundingClientRect();
            this.stop();
            void this.store.seek(timeFromX(e.clientX - r.left, r.width, d, this.fps()));
        };
        strip.addEventListener("pointerdown", (e) => {
            if (e.button !== 0)
                return;
            strip.setPointerCapture(e.pointerId);
            to(e);
            const move = (m) => to(m);
            const up = () => { strip.removeEventListener("pointermove", move); strip.removeEventListener("pointerup", up); };
            strip.addEventListener("pointermove", move);
            strip.addEventListener("pointerup", up);
        });
    }
    laneHead(lane, element) {
        const label = laneLabel(lane.property);
        const head = h("div", { class: "tl-row tl-lane-head", data: { property: lane.property } }, h("span", { class: "tl-name", text: label }));
        if (lane.editable) {
            const mode = h("select", { class: "tl-mode", attrs: { "aria-label": `How ${label} changes between keys`, title: "How the value changes between keys" } }, (lane.property === "visibility" ? ["step"] : ["smooth", "linear", "step"]).map((i) => h("option", { value: i, text: INTERPOLATION_LABEL[i], selected: i === lane.interpolation })));
            mode.addEventListener("change", () => { void this.store.animate("interpolation", { id: lane.object_id, property: lane.property, interpolation: mode.value }); });
            head.append(mode, h("button", { type: "button", class: "tl-del", attrs: { "aria-label": `Delete the ${label} track of ${element}`, title: "Delete track" }, on: { click: () => { void this.store.animate("delete_track", { id: lane.object_id, property: lane.property }); } }, text: "✕" }));
        }
        else
            head.append(h("span", { class: "muted", text: lane.interpolation }));
        return head;
    }
    laneStrip(lane, d) {
        const strip = h("div", { class: "tl-row tl-track", data: { object: lane.object_id, property: lane.property } });
        const left = (t) => `${d > 0 ? (t / d) * 100 : 0}%`;
        for (const k of lane.keys) {
            const selected = this.selectedKey?.id === lane.object_id && this.selectedKey.property === lane.property && Math.abs(this.selectedKey.time - k.time) < 1e-9;
            const key = h("button", { type: "button", class: `tl-key${selected ? " selected" : ""}`, data: { time: String(k.time), property: lane.property },
                attrs: { title: `${fmt(k.time)} s: ${describeValue(lane.property, k.value)} (drag to move, Delete to remove)`, "aria-label": `${laneLabel(lane.property)} key at ${fmt(k.time)} seconds`, "aria-pressed": String(selected) } });
            key.style.setProperty("left", left(k.time));
            if (lane.editable)
                this.dragKey(key, strip, lane, k.time, d);
            else
                key.addEventListener("click", () => { this.stop(); void this.store.seek(k.time); });
            strip.append(key);
        }
        strip.append(h("div", { class: "playhead" }));
        this.dragPlayhead(strip, d);
        return strip;
    }
    /** A key is selected by pressing it (which also selects its element and moves the playhead there) and moved by dragging it along the strip. */
    dragKey(key, strip, lane, time, d) {
        key.addEventListener("pointerdown", (e) => {
            if (e.button !== 0)
                return;
            e.stopPropagation();
            key.setPointerCapture(e.pointerId);
            this.stop();
            let target = time, moved = false;
            const rect = strip.getBoundingClientRect();
            const move = (m) => {
                const t = timeFromX(m.clientX - rect.left, rect.width, d, this.fps());
                if (Math.abs(m.clientX - e.clientX) > 3)
                    moved = true;
                if (moved) {
                    target = t;
                    key.style.setProperty("left", `${d > 0 ? (t / d) * 100 : 0}%`);
                }
            };
            const up = async () => {
                key.removeEventListener("pointermove", move);
                key.removeEventListener("pointerup", onUp);
                this.selectedKey = { id: lane.object_id, property: lane.property, time };
                if (moved && Math.abs(target - time) > 1e-9) {
                    this.selectedKey = { ...this.selectedKey, time: target };
                    if (!(await this.store.animate("move_key", { id: lane.object_id, property: lane.property, time, to: target })))
                        key.style.setProperty("left", `${d > 0 ? (time / d) * 100 : 0}%`);
                    await this.store.seek(target);
                }
                else {
                    if (this.selectedId() !== lane.object_id)
                        await this.store.select({ kind: "model", ids: [lane.object_id] });
                    await this.store.seek(time);
                    this.signature = "";
                    this.render();
                }
                this.el.focus();
            };
            const onUp = () => { void up(); };
            key.addEventListener("pointermove", move);
            key.addEventListener("pointerup", onUp);
        });
    }
    // ------------------------------------------------------------------------------------------------------------------- pose panel
    posePanel() {
        const id = this.selectedId();
        const pose = this.store.pose;
        const panel = h("div", { class: "tl-pose", attrs: { "aria-label": "Pose" } });
        const hint = h("ol", { class: "tl-steps" }, h("li", { text: "Select an element in the 3D view." }), h("li", { text: "Move the playhead to a time." }), h("li", { text: "Change its Pose here, or drag it in the 3D view. A key is made for you." }));
        if (!id || !pose || pose.id !== id) {
            panel.append(h("strong", { text: "Pose" }), hint, this.settingsRow());
            return panel;
        }
        const keyed = new Set(pose.keyed);
        panel.append(h("div", { class: "tl-pose-head" }, h("strong", { class: "tl-keylabel", text: `${pose.name} at ${fmt(this.store.animTime)} s` }), h("label", { class: "tl-field" }, this.checkbox("anim-autokey", this.autoKey, (v) => { this.autoKey = v; this.signature = ""; this.render(); }), "Auto-key"), h("label", { class: "tl-field", attrs: { title: "Drag the selected element in the 3D view to key where it goes. Hold Shift to lift it." } }, this.checkbox("anim-drag3d", this.store.dragToKey, (v) => { this.store.dragToKey = v; this.signature = ""; this.render(); }), "Drag in 3D view")));
        const rows = [];
        const row = (property, fields) => {
            const isKeyed = keyed.has(property);
            const diamond = h("button", { type: "button", class: `tl-diamond${isKeyed ? " on" : ""}`, data: { property }, attrs: { "aria-pressed": String(isKeyed), "aria-label": `${isKeyed ? "Remove the" : "Set a"} ${laneLabel(property)} key at this time`, title: isKeyed ? "Keyed here. Click to remove this key." : "Click to key the current value here." } });
            diamond.addEventListener("click", () => { void (isKeyed ? this.store.animate("delete_key", { id, property, time: this.store.animTime }) : this.keyNow(id, property, this.readProperty(property, pose.pose))); });
            rows.push(h("div", { class: "tl-pose-row", data: { property } }, diamond, h("span", { class: "tl-pose-label", text: laneLabel(property) }), ...fields));
        };
        const vec = (property, labels, value, step) => {
            const inputs = labels.map((l, i) => this.numberInput(`anim-${property}-${l.toLowerCase()}`, `${laneLabel(property)} ${l}`, value[i] ?? 0, step));
            for (const input of inputs)
                input.addEventListener("change", () => this.edit(id, property, inputs.map((x) => x.valueAsNumber)));
            return inputs.map((input, i) => h("label", { class: "tl-field" }, labels[i], input));
        };
        row("translation", [...vec("translation", ["X", "Y", "Z"], pose.pose.translation, "0.1"), h("span", { class: "muted", text: "m" })]);
        const turn = this.numberInput("anim-turn", "Turn", pose.pose.turn, "5");
        turn.addEventListener("change", () => this.edit(id, "turn", turn.valueAsNumber));
        row("turn", [turn, h("span", { class: "muted", text: "° about the vertical axis" })]);
        row("scale", vec("scale", ["X", "Y", "Z"], pose.pose.scale, "0.1"));
        const shown = this.checkbox("anim-visible", pose.pose.visibility, (v) => this.edit(id, "visibility", v));
        row("visibility", [h("label", { class: "tl-field" }, shown, "Shown")]);
        panel.append(...rows);
        if (!this.autoKey)
            panel.append(h("p", { class: "muted", text: "Auto-key is off: editing a field changes nothing until you click its diamond." }));
        panel.append(this.presetsView(id), this.settingsRow());
        return panel;
    }
    checkbox(id, checked, on) {
        const el = h("input", { type: "checkbox", id });
        el.checked = checked;
        el.addEventListener("change", () => on(el.checked));
        return el;
    }
    numberInput(id, label, value, step) {
        const el = h("input", { type: "number", id, attrs: { step, "aria-label": label } });
        el.value = String(Math.round(value * 1e4) / 1e4);
        return el;
    }
    readProperty(property, pose) {
        return property === "translation" ? pose.translation : property === "scale" ? pose.scale : property === "turn" ? pose.turn : pose.visibility;
    }
    /** A field was changed: with Auto-key on it becomes a key at the playhead; with it off, nothing is written until the diamond is clicked. */
    edit(id, property, value) {
        if (Array.isArray(value) ? value.some((v) => !Number.isFinite(v)) : typeof value === "number" && !Number.isFinite(value)) {
            this.store.setStatus("Enter a number");
            this.signature = "";
            this.render();
            return;
        }
        if (!this.autoKey) {
            this.store.setStatus("Auto-key is off. Click the diamond to key this value.");
            return;
        }
        void this.keyNow(id, property, value);
    }
    async keyNow(id, property, value) {
        this.stop();
        const out = await this.store.animate("key", { id, property, time: this.store.animTime, value });
        const notes = out?.result?.notes;
        if (notes?.length)
            this.store.setStatus(notes.join(" "));
    }
    settingsRow() {
        const name = h("input", { type: "text", id: "anim-marker-name", attrs: { "aria-label": "Marker name", maxlength: "80", placeholder: "Marker name" } });
        return h("div", { class: "tl-settings" }, name, h("button", { type: "button", id: "anim-add-marker", class: "tool-inline", on: { click: () => { void this.store.animate("marker", { time: this.store.animTime, name: name.value.trim() || `Marker ${fmt(this.store.animTime)} s` }); } }, text: "Add marker here" }));
    }
    presetsView(id) {
        const a = this.store.animation;
        const p = this.preset;
        const d = this.duration();
        if (!p.start)
            p.start = String(this.store.animTime);
        if (!p.end)
            p.end = String(d);
        const kinds = Object.entries(a.presets);
        const kind = h("select", { id: "anim-preset-kind", attrs: { "aria-label": "Quick motion" } }, kinds.map(([k, text]) => h("option", { value: k, text: text.split(":")[0] ?? k, title: text, selected: k === p.kind })));
        kind.addEventListener("change", () => { p.kind = kind.value; this.presetsOpen = true; this.signature = ""; this.render(); }); // the list is only reachable while open; do not wait for the details' own toggle event, or the rebuild folds it away
        const field = (idn, label, value, set, step = "0.1") => {
            const input = h("input", { type: "number", id: idn, attrs: { step, "aria-label": label } });
            input.value = value;
            input.addEventListener("input", () => set(input.value));
            return h("label", { class: "tl-field" }, label, input);
        };
        const params = [];
        if (p.kind === "slide")
            ["X", "Y", "Z"].forEach((n, i) => params.push(field(`anim-preset-${n.toLowerCase()}`, `${n} (m)`, p.by[i] ?? "0", (v) => { p.by[i] = v; })));
        if (p.kind === "spin")
            params.push(field("anim-preset-degrees", "Degrees", p.degrees, (v) => { p.degrees = v; }, "15"));
        if (p.kind === "grow")
            params.push(field("anim-preset-factor", "Factor", p.factor, (v) => { p.factor = v; }));
        const apply = h("button", { type: "button", id: "anim-preset-apply", class: "tool-inline", text: "Apply", on: { click: () => { void this.applyPreset(id); } } });
        const box = h("details", { class: "tl-presets" }, h("summary", { text: "Quick motion" }), h("div", { class: "tl-preset-row" }, kind, ...params, field("anim-preset-start", "From (s)", p.start, (v) => { p.start = v; }), field("anim-preset-end", "To (s)", p.end, (v) => { p.end = v; }), apply));
        box.open = this.presetsOpen; // a rebuild must not fold it away while the person is filling it in
        box.addEventListener("toggle", () => { this.presetsOpen = box.open; });
        return box;
    }
    async applyPreset(id) {
        const p = this.preset;
        const params = {};
        if (p.kind === "slide")
            params["by"] = p.by.map((v) => Number(v) || 0);
        if (p.kind === "spin")
            params["degrees"] = Number(p.degrees);
        if (p.kind === "grow")
            params["factor"] = Number(p.factor);
        this.stop();
        const out = await this.store.animate("preset", { id, kind: p.kind, start: Number(p.start), end: Number(p.end), params });
        if (out)
            await this.store.seek(Number(p.end));
    }
    // ------------------------------------------------------------------------------------------------------------------- keyboard
    key(e) {
        const t = e.target;
        if (t.matches("input:not([type=checkbox]), select, textarea"))
            return;
        if (e.ctrlKey || e.metaKey || e.altKey)
            return;
        const jump = e.shiftKey ? this.fps() : 1;
        if (e.key === " ") {
            e.preventDefault();
            this.toggle();
        }
        else if (e.key === "ArrowLeft") {
            e.preventDefault();
            this.step(-jump);
        }
        else if (e.key === "ArrowRight") {
            e.preventDefault();
            this.step(jump);
        }
        else if (e.key === "Home") {
            e.preventDefault();
            this.stop();
            void this.store.seek(0);
        }
        else if (e.key === "End") {
            e.preventDefault();
            this.stop();
            void this.store.seek(this.duration());
        }
        else if ((e.key === "Delete" || e.key === "Backspace") && this.selectedKey) {
            e.preventDefault();
            const k = this.selectedKey;
            this.selectedKey = null;
            void this.store.animate("delete_key", { id: k.id, property: k.property, time: k.time }).then(() => this.store.loadPose());
        }
    }
    // ------------------------------------------------------------------------------------------------------------------- playback
    toggle() { if (this.store.playing)
        this.stop();
    else
        this.start(); }
    start() {
        const d = this.duration();
        if (d <= 0)
            return;
        if (this.store.animTime >= d)
            void this.store.seek(0);
        this.store.playing = true;
        this.last = performance.now();
        if (this.playButton)
            this.playButton.textContent = "Pause";
        const loop = this.store.animation?.settings.loop ?? false;
        const tick = (now) => {
            if (!this.store.playing)
                return;
            if (!this.waiting) { // one request at a time: the picture may lag, the time stays true
                const dt = ((now - this.last) / 1000) * this.speed;
                this.last = now;
                this.waiting = true;
                let next = this.store.animTime + dt;
                if (next >= d) {
                    if (loop)
                        next -= d;
                    else {
                        void this.store.seek(d).finally(() => { this.waiting = false; this.stop(); });
                        return;
                    }
                }
                void this.store.seek(next).finally(() => { this.waiting = false; });
            }
            this.raf = requestAnimationFrame(tick);
        };
        this.raf = requestAnimationFrame(tick);
    }
    stop() {
        const was = this.store.playing;
        this.store.playing = false;
        if (this.raf)
            cancelAnimationFrame(this.raf);
        this.raf = 0;
        if (this.playButton)
            this.playButton.textContent = "Play";
        if (was)
            void this.store.loadPose().then(() => { this.signature = ""; this.render(); });
    }
    // ------------------------------------------------------------------------------------------------------------------- export
    async export() {
        if (this.exporter) {
            this.exporter.abort();
            return;
        }
        const capture = this.capture();
        if (!capture) {
            toast("The 3D view is not available, so there is nothing to render.", { kind: "error" });
            return;
        }
        this.stop();
        const controller = new AbortController();
        this.exporter = controller;
        this.signature = "";
        this.render();
        const progress = (done, total, what) => {
            const el = this.el.querySelector("#anim-export-progress");
            if (el)
                el.textContent = `${what}: ${done} / ${total}`;
        };
        try {
            const result = await exportAnimation({ store: this.store, capture, kind: this.exportKind, signal: controller.signal, onProgress: progress });
            saveBlob(result.blob, result.name);
            toast(`Exported ${result.frames} frames at ${result.fps} frames/s as ${result.name}.`, { kind: "ok", timeoutMs: 4000 });
        }
        catch (error) {
            if (error instanceof DOMException && error.name === "AbortError")
                toast("Export cancelled.", { kind: "info", timeoutMs: 2500 });
            else
                toast(error instanceof Error ? error.message : "The export failed.", { kind: "error" });
        }
        finally {
            this.exporter = null;
            await this.store.seek(this.store.animTime); // put the view back to the playhead
            this.signature = "";
            this.render();
        }
    }
}
