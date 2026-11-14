/** Curtain walls: the creation dialog. It only gathers the choices and shows what the server says they make (`/curtainwall/preview`); the server holds every rule,
 *  the Stabalux profile library (`/curtainwall/profiles`, each size with the catalogue page it was read from) and builds the element. Once made, members, panels and
 *  doors are edited in Properties. */
import { clear, h, labelled, s } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
const MM_PER = { mm: 1, cm: 10, m: 1000 };
export const MODES = [["centre", "Centre to centre (hart-op-hart)"], ["max", "Maximum spacing"], ["min", "Minimum spacing"], ["count", "Fixed number of interior members"]];
export const ALIGNS = [["start", "Remainder at the end"], ["centre", "Remainder split at both ends"], ["end", "Remainder at the start"]];
export const JOINS = [["mullions", "Mullions continuous"], ["transoms", "Transoms continuous"], ["mitre", "Mitred at the corners"]];
export const FRAME_MATERIALS = ["Aluminium", "Steel", "Timber"];
export const PANEL_TYPES = ["Window", "Opaque panel", "Louvre"];
export const PANEL_MATERIALS = ["Glass", "Aluminium", "Steel", "Timber"];
export const DOOR_TYPES = ["Single Flush Door", "Double Door", "Sliding Door"];
/** The corners two curtain walls can meet in (the server's ``CORNER_TYPES``) and the angles, inside the corner, each can be built for. */
export const CORNER_LABELS = { L: "L-Corner", quad: "Quad Corner", trapezoid: "Trapezoid Corner", V: "V-Corner" };
export const CORNER_RANGES = { L: [45, 135], quad: [30, 150], trapezoid: [60, 175], V: [15, 165] };
let library = null;
async function profiles(store) {
    if (library)
        return library;
    try {
        library = await store.api.get("/curtainwall/profiles");
        return library;
    }
    catch (e) {
        toast(e instanceof Error ? e.message : "Could not read the profile library.", { kind: "error" });
        return null;
    }
}
/** The front of the wall with the members the server computed: frame rectangles over a glazing tint. Pure drawing of numbers the server returned. */
export function elevationSvg(pv) {
    const L = pv?.length ?? 6000, H = pv?.height ?? 3000, w = pv?.frame_width_mm ?? 50;
    const pad = Math.max(L, H) * 0.04;
    const svg = s("svg", { attrs: { viewBox: `${-pad} ${-pad} ${L + 2 * pad} ${H + 2 * pad}`, role: "img", "aria-label": "Front of the curtain wall with its members", preserveAspectRatio: "xMidYMid meet" }, css: { width: "100%", height: "16rem", "max-height": "40vh" } });
    svg.append(s("rect", { attrs: { x: 0, y: 0, width: L, height: H, fill: "var(--accent, #2f6fde)", "fill-opacity": 0.12 } }));
    const ink = { fill: "currentColor", "fill-opacity": 0.75 };
    for (const u of pv?.mullions ?? [])
        svg.append(s("rect", { attrs: { x: u - w / 2, y: 0, width: w, height: H, ...ink }, data: { member: "mullion" } }));
    for (const z of pv?.transoms ?? [])
        svg.append(s("rect", { attrs: { x: 0, y: H - z - w / 2, width: L, height: w, ...ink }, data: { member: "transom" } }));
    return svg;
}
/** The dialog. `start` and `end` (drawing units) come from the plan tool; without them the person types the line. `level` is the plan's level. */
export async function curtainWallDialog(store, from = {}) {
    if (!store.project)
        return;
    const lib = await profiles(store);
    if (!lib)
        return;
    const unit = store.unit, perUnit = MM_PER[unit];
    const num = (value, step = "any", min) => h("input", { type: "number", value, step, ...(min ? { min } : {}), attrs: { autocomplete: "off" } });
    const sel = (items, value) => h("select", {}, ...items.map(([v, l]) => h("option", { value: v, text: l, ...(v === value ? { selected: true } : {}) })));
    const strs = (items, value) => sel(items.map((v) => [v, v]), value);
    const level = sel(store.levels.map((l) => [l.id, l.name]), from.level ?? store.levels[0]?.id);
    const chain = from.points && from.points.length >= 2 ? from.points : null; // a run of connected curtain walls drawn in the plan
    const start = chain?.[0] ?? from.start ?? [0, 0], end = chain?.[1] ?? from.end ?? [6000 / perUnit, 0];
    const segments = chain ? chain.length - 1 + (from.closed ? 1 : 0) : 1;
    const corner = sel([["auto", "Automatic (the corner that suits each angle)"], ...Object.entries(CORNER_LABELS)], "auto");
    const sx = num(String(start[0])), sy = num(String(start[1])), ex = num(String(end[0])), ey = num(String(end[1]));
    const height = num("3000", "50", "100"), baseOffset = num("0", "10");
    const name = h("input", { type: "text", value: "Curtain Wall", attrs: { autocomplete: "off", maxlength: "80" } });
    const vMode = sel(MODES, "centre"), hMode = sel(MODES, "centre");
    const vValue = num("1200", "any", "0"), hValue = num("2100", "any", "0");
    const vAlign = sel(ALIGNS, "start"), hAlign = sel(ALIGNS, "start");
    const material = strs(FRAME_MATERIALS, "Aluminium");
    const profile = h("select", {});
    const fwidth = num("50", "1", "10"), fdepth = num("90", "1", "10"), radius = num("0", "0.5", "0");
    const join = sel(JOINS, "mullions");
    const ptype = strs(PANEL_TYPES, "Window"), pmat = strs(PANEL_MATERIALS, "Glass");
    const pthick = num("24", "1", "2"), poffset = num("0", "1");
    const dtype = strs(DOOR_TYPES, "Single Flush Door");
    const dhinge = sel([["start", "Start side"], ["end", "End side"]], "start"), dswing = strs(["left", "right"], "left");
    const source = h("p", { class: "muted", attrs: { role: "note" } });
    const status = h("p", { class: "muted", attrs: { role: "status", "aria-live": "polite" } });
    const picture = h("div", {});
    const modeHints = () => {
        for (const [mode, value, align] of [[vMode, vValue, vAlign], [hMode, hValue, hAlign]]) {
            const count = mode.value === "count";
            value.step = count ? "1" : "any";
            align.disabled = mode.value !== "centre";
        }
    };
    const fillProfiles = (keep) => {
        clear(profile);
        profile.append(h("option", { value: "custom", text: "Custom size (no catalogue source)" }));
        for (const p of lib.profiles.filter((x) => x.material === material.value)) {
            profile.append(h("option", { value: p.id, text: `${p.system} ${p.name} — ${p.width_mm}${p.depth_mm ? ` × ${p.depth_mm}` : ""} mm (p. ${p.page})` }));
        }
        profile.value = [...profile.options].some((o) => o.value === keep) ? keep : "custom";
    };
    const applyProfile = () => {
        const p = lib.profiles.find((x) => x.id === profile.value);
        fwidth.disabled = !!p;
        fdepth.disabled = !!p && p.depth_mm !== null;
        if (p) {
            fwidth.value = String(p.width_mm);
            if (p.depth_mm !== null)
                fdepth.value = String(p.depth_mm);
        }
        source.textContent = p ? p.provenance : "Custom size entered by hand: it carries no catalogue source.";
    };
    const defaults = { Aluminium: "stabalux-al5090", Steel: "stabalux-sr5090-2", Timber: "stabalux-h-60" };
    fillProfiles(defaults.Aluminium);
    applyProfile();
    modeHints();
    const request = () => {
        const p = lib.profiles.find((x) => x.id === profile.value);
        const mmValue = (mode, v) => (mode === "count" ? Number(v) : Number(v));
        const out = {
            ...(chain ? { points: chain, closed: !!from.closed, corner: corner.value } : { start: [Number(sx.value), Number(sy.value)], end: [Number(ex.value), Number(ey.value)] }), level: level.value, height: Number(height.value), base_offset: Number(baseOffset.value), name: name.value.trim() || "Curtain Wall",
            v_mode: vMode.value, v_value: mmValue(vMode.value, vValue.value), v_align: vAlign.value, h_mode: hMode.value, h_value: mmValue(hMode.value, hValue.value), h_align: hAlign.value,
            frame_material: material.value, corner_radius: Number(radius.value), join: join.value,
            panel_type: ptype.value, panel_material: pmat.value, panel_thickness: Number(pthick.value), panel_offset: Number(poffset.value),
            door_type: dtype.value, door_hinge: dhinge.value, door_swing: dswing.value,
        };
        if (p) {
            out.frame_profile = p.id;
            if (p.depth_mm === null)
                out.frame_depth = Number(fdepth.value);
        }
        else {
            out.frame_profile = "custom";
            out.frame_width = Number(fwidth.value);
            out.frame_depth = Number(fdepth.value);
        }
        return out;
    };
    let seq = 0, latest = null, timer;
    const paint = () => { clear(picture); picture.append(elevationSvg(latest)); };
    const refresh = async () => {
        const mine = ++seq;
        try {
            const out = await store.api.post("/curtainwall/preview", { ...request(), start: [Number(sx.value), Number(sy.value)], end: [Number(ex.value), Number(ey.value)] });
            if (mine !== seq)
                return;
            latest = out;
            status.textContent = `${out.mullions.length} vertical and ${out.transoms.length} horizontal members, ${out.panels} panel${out.panels === 1 ? "" : "s"}. Bays across: ${[...new Set(out.bays_v_mm.map((b) => Math.round(b)))].join(", ")} mm; up: ${[...new Set(out.bays_h_mm.map((b) => Math.round(b)))].join(", ")} mm.`;
            status.classList.remove("error");
        }
        catch (e) {
            if (mine === seq) {
                latest = null;
                status.textContent = e instanceof Error ? e.message : "Could not preview.";
                status.classList.add("error");
            }
        }
        paint();
    };
    const sync = () => { window.clearTimeout(timer); timer = window.setTimeout(() => { void refresh(); }, 120); };
    vMode.addEventListener("change", () => { vValue.value = vMode.value === "count" ? "3" : "1200"; modeHints(); sync(); });
    hMode.addEventListener("change", () => { hValue.value = hMode.value === "count" ? "2" : "2100"; modeHints(); sync(); });
    material.addEventListener("change", () => { fillProfiles(defaults[material.value]); applyProfile(); sync(); });
    profile.addEventListener("change", () => { applyProfile(); sync(); });
    for (const el of [sx, sy, ex, ey, level, height, baseOffset, vValue, hValue, vAlign, hAlign, fwidth, fdepth, radius, join, ptype, pmat, pthick, poffset, dtype, dhinge, dswing])
        el.addEventListener("change", sync);
    const two = (a, b) => h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, a, b);
    const group = (title, ...kids) => h("fieldset", { css: { display: "grid", gap: "0.6rem", border: "1px solid var(--line, #8884)", padding: "0.5rem 0.75rem" } }, h("legend", { text: title }), ...kids);
    const handle = openDialog({
        title: "Curtain wall", size: "wide", focus: () => name,
        body: h("div", { css: { display: "grid", gridTemplateColumns: "minmax(18rem, 1fr) minmax(16rem, 1fr)", gap: "1.25rem", alignItems: "start" } }, h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("Name", name), labelled("Level", level), chain ? group("Where", h("p", { text: `${segments} connected curtain walls along the ${chain.length} points picked in the plan${from.closed ? " (closed)" : ""}. Each stays its own element; the preview shows the first.` }), labelled("Corners where they meet", corner, { hint: "Each corner can be changed afterwards: right-click a curtain wall, or select it and use Properties." }), two(labelled("Height (mm)", height), labelled("Base offset (mm)", baseOffset)))
            : group(`Where (${unit})`, two(labelled("Start x", sx), labelled("Start y", sy)), two(labelled("End x", ex), labelled("End y", ey)), two(labelled("Height (mm)", height), labelled("Base offset (mm)", baseOffset))), group("Vertical members (mullions)", labelled("Spacing rule", vMode, { hint: "Centre to centre is the distance between member axes; the other rules divide the span in equal bays." }), two(labelled("Distance (mm) or number", vValue), labelled("Remainder", vAlign))), group("Horizontal members (transoms)", labelled("Spacing rule", hMode), two(labelled("Distance (mm) or number", hValue), labelled("Remainder", hAlign))), group("Frame", two(labelled("Material", material), labelled("Profile (Stabalux catalogue)", profile)), source, two(labelled("Width of the face (mm)", fwidth), labelled("Thickness through the wall (mm)", fdepth)), two(labelled("Corner radius (mm)", radius), labelled("Join condition", join))), group("Panels", two(labelled("Panel type", ptype), labelled("Panel material", pmat)), two(labelled("Panel thickness (mm)", pthick), labelled("Panel offset (mm)", poffset)), h("details", {}, h("summary", { text: "A field changed into a door starts as" }), two(labelled("Door type", dtype), labelled("Hinge", dhinge)), labelled("Swing", dswing)))), h("div", { css: { display: "grid", gap: "0.5rem", position: "sticky", top: "0" } }, picture, status, h("p", { class: "muted", text: "Remove or add a member, or turn a field into a door, afterwards in Properties. Profile sizes are as the Stabalux catalogues print them; they are not approvals of a complete system." }))),
        actions: [{ label: segments > 1 ? `Add ${segments} curtain walls` : "Add curtain wall", kind: "primary", run: async () => {
                    window.clearTimeout(timer);
                    await refresh();
                    if (!latest) {
                        toast(status.textContent || "This curtain wall cannot be made.", { kind: "error" });
                        return undefined;
                    }
                    const out = await store.run("model.add_curtainwall", request());
                    if (!out)
                        return undefined;
                    const made = out.result;
                    const ids = made?.ids?.length ? made.ids : made?.id ? [made.id] : [];
                    if (ids.length)
                        await store.select({ kind: "model", ids });
                    if (ids.length > 1)
                        toast(`${ids.length} curtain walls made and joined. Click one to edit it; right-click it to change a corner.`, { kind: "ok" });
                    return true;
                } }],
    });
    paint();
    void refresh();
    await handle.result;
    window.clearTimeout(timer);
}
/** Change the corner at one end of a curtain wall where it meets another: L, Quad, Trapezoid or V (or automatic), with its sizes. The server checks the
 *  corner against the angle the walls meet at and refuses one that cannot be built there, naming the range. */
export async function cornerDialog(store, id, end = "end") {
    const num = (placeholder) => h("input", { type: "number", step: "1", min: "10", attrs: { autocomplete: "off", placeholder } });
    const which = h("select", {}, h("option", { value: "start", text: "Start", ...(end === "start" ? { selected: true } : {}) }), h("option", { value: "end", text: "End", ...(end === "end" ? { selected: true } : {}) }));
    const type = h("select", {}, h("option", { value: "auto", text: "Automatic" }), ...Object.entries(CORNER_LABELS).map(([v, l]) => h("option", { value: v, text: `${l} (${CORNER_RANGES[v]?.[0]}–${CORNER_RANGES[v]?.[1]}°)` })));
    const depthA = num("auto"), depthB = num("auto"), leg = num("auto"), legA = num("auto"), legB = num("auto"), thick = num("auto");
    const rows = {
        quad: h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" } }, labelled("Depth along this wall (mm)", depthA), labelled("Depth along the other (mm)", depthB)),
        trapezoid: labelled("Leg (mm)", leg),
        L: h("div", { css: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem" } }, labelled("Leg on this wall (mm)", legA), labelled("Leg on the other (mm)", legB), labelled("Thickness (mm)", thick)),
    };
    const sizes = h("div", {});
    const sync = () => { clear(sizes); const r = rows[type.value === "V" ? "L" : type.value]; if (r)
        sizes.append(r); };
    type.addEventListener("change", sync);
    sync();
    const handle = openDialog({
        title: "Curtain wall corner", size: "narrow", focus: () => type,
        body: h("div", { css: { display: "grid", gap: "0.9rem" } }, labelled("At the wall's", which), labelled("Corner", type), sizes, h("p", { class: "muted", text: "Leave a size empty to keep it as it is (or as the corner type sizes it). The other wall's end follows." })),
        actions: [{ label: "Apply", kind: "primary", run: async () => {
                    const params = { id, end: which.value, type: type.value };
                    const put = (key, el) => { if (el.value)
                        params[key] = Number(el.value); };
                    if (type.value === "quad") {
                        put("depth_a", depthA);
                        put("depth_b", depthB);
                    }
                    if (type.value === "trapezoid")
                        put("leg", leg);
                    if (type.value === "L" || type.value === "V") {
                        put("leg_a", legA);
                        put("leg_b", legB);
                        put("thickness", thick);
                    }
                    return (await store.run("model.set_curtainwall_corner", params)) ? true : undefined;
                } }],
    });
    await handle.result;
}
