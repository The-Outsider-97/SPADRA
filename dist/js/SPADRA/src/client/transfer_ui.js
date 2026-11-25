/** Copy, mirror and paste between elements: the properties (onto existing compatible elements, nothing created) and the elements themselves (a copy, made
 *  new). The two are different menu entries on purpose: Properties changes elements that are already there; Element makes new ones.
 *
 *  Every target is reported on its own: what changed, what was skipped and why, what failed and why. Nothing here decides compatibility; the server does. */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import { UNIT_MM } from "./geom2d.js";
const AXIS_TEXT = {
    side: "Across its thickness: the inside becomes the outside (layer order, offsets, handing, near and far)",
    length: "Along its length: start and end swap (a hinge on the left goes to the right)",
    both: "Both: across the thickness and along the length",
};
/** Ask what to transfer, then do it for every compatible target and report each one. */
export async function pasteProperties(store, source, targets, mode) {
    if (!store.project || source.project !== store.project.id)
        return;
    const destinations = targets.filter((t) => t !== source.id);
    if (!destinations.length) {
        toast("Select at least one destination other than the source.", { kind: "info" });
        return;
    }
    let info;
    try {
        info = await store.api.post("/properties/transfer-info", { source: source.id });
    }
    catch (error) {
        toast(error instanceof Error ? error.message : "The source could not be read.", { kind: "error" });
        return;
    }
    if (mode === "mirror" && !info.mirrors) {
        toast(`A ${info.category} has no side or direction to mirror. Use Paste Properties to copy as it is.`, { kind: "info" });
        return;
    }
    const boxes = info.groups.map((g) => {
        const input = h("input", { type: "checkbox", checked: g.has_values && g.id !== "dimensions" && g.id !== "general" && g.id !== "connections", disabled: !g.has_values, attrs: { "aria-label": g.label, value: g.id } });
        return { id: g.id, input, row: h("label", { class: "check" }, input, h("span", { text: g.has_values ? g.label : `${g.label} (the source has none)` })) };
    });
    const axis = h("select", { attrs: { "aria-label": "Mirror across" } }, ...info.axes.map((a) => h("option", { value: a, text: a === "side" ? "Its sides (thickness)" : a === "length" ? "Its length (start ↔ end)" : "Both" })));
    const axisNote = h("p", { class: "muted", text: AXIS_TEXT[axis.value] ?? "" });
    axis.addEventListener("change", () => { axisNote.textContent = AXIS_TEXT[axis.value] ?? ""; });
    const unit = store.unit;
    const planeOption = info.has_direction ? [h("option", { value: "plane", text: "A reference plane (two points of the plan)" })] : [];
    axis.append(...planeOption);
    const pf = (name, value) => h("input", { type: "number", value, step: "any", attrs: { "aria-label": `${name} (${unit})` } });
    const [ax, ay, bx, by] = [pf("First point X", "0"), pf("First point Y", "0"), pf("Second point X", "0"), pf("Second point Y", "1000")];
    const planeRows = h("div", {}, h("label", { class: "field" }, h("span", { text: `First point X (${unit})` }), ax), h("label", { class: "field" }, h("span", { text: `First point Y (${unit})` }), ay), h("label", { class: "field" }, h("span", { text: `Second point X (${unit})` }), bx), h("label", { class: "field" }, h("span", { text: `Second point Y (${unit})` }), by), h("p", { class: "muted", text: "A vertical plane through these two points. Along the element's length it swaps the faces; square to its length it swaps the ends. A plane at any other angle is refused." }));
    const showPlane = () => { planeRows.hidden = axis.value !== "plane"; axisNote.hidden = axis.value === "plane"; };
    axis.addEventListener("change", showPlane);
    showPlane();
    const body = h("div", {}, h("p", { text: `${mode === "mirror" ? "Mirror" : "Copy"} the properties of ${source.name} onto ${destinations.length} selected element${destinations.length === 1 ? "" : "s"}. No element is created, moved, replaced or renamed; each keeps its place, host, level and identity.` }), ...boxes.map((b) => b.row), mode === "mirror" ? h("label", { class: "field" }, h("span", { text: "Mirror across" }), axis) : null, mode === "mirror" ? axisNote : null, mode === "mirror" ? planeRows : null, h("details", {}, h("summary", { text: "What is never transferred" }), h("ul", {}, ...info.not_transferred.map((t) => h("li", { text: t })))));
    const choice = await openDialog({ title: mode === "mirror" ? "Mirror properties" : "Paste properties", size: "narrow", body, actions: [{ label: mode === "mirror" ? "Mirror onto selection" : "Paste onto selection", kind: "primary", run: () => {
                    const groups = boxes.filter((b) => b.input.checked).map((b) => b.id);
                    if (!groups.length) {
                        toast("Choose at least one group of properties.", { kind: "info" });
                        return undefined;
                    }
                    if (mode === "mirror" && axis.value === "plane") {
                        const [x1, y1, x2, y2] = [Number(ax.value), Number(ay.value), Number(bx.value), Number(by.value)];
                        const pts = [[x1, y1], [x2, y2]];
                        if (![x1, y1, x2, y2].every(Number.isFinite)) {
                            toast("Enter numbers for the plane.", { kind: "info" });
                            return undefined;
                        }
                        if (Math.hypot(x2 - x1, y2 - y1) < 1e-6) {
                            toast("The two points of the plane must be apart.", { kind: "info" });
                            return undefined;
                        }
                        return { groups, axis: "side", plane: pts };
                    }
                    return { groups, axis: axis.value };
                } }] }).result;
    if (!choice)
        return;
    const out = await store.run("model.paste_properties", { source: source.id, destinations, groups: choice.groups, mode, axis: choice.axis, ...(choice.plane ? { plane: choice.plane } : {}) });
    if (!out)
        return;
    await reportTransfer(out.result);
}
/** One line for the status area and, when anything was skipped or failed, a dialog that names every target and why. */
export async function reportTransfer(r) {
    const parts = [`${r.applied.length} updated`, ...(r.skipped.length ? [`${r.skipped.length} skipped`] : []), ...(r.failed.length ? [`${r.failed.length} failed`] : [])];
    if (!r.skipped.length && !r.failed.length && !r.applied.some((a) => a.no_equivalent.length)) {
        toast(`${r.mode === "mirror" ? "Mirrored" : "Pasted"} properties: ${parts.join(", ")}.`, { kind: "ok", timeoutMs: 3500 });
        return;
    }
    const list = (title, rows) => rows.length
        ? h("section", {}, h("h3", { text: title }), h("ul", {}, ...rows.map((x) => h("li", {}, h("strong", { text: x.name }), ` — ${x.text}`)))) : null;
    await openDialog({ title: `${r.mode === "mirror" ? "Mirror" : "Paste"} properties: ${parts.join(", ")}`, size: "wide", cancelLabel: "Close", body: h("div", {}, list("Updated", r.applied.map((a) => ({ name: a.name, text: [a.changed.length ? a.changed.join(", ") : "nothing differed", a.no_equivalent.length ? `no equivalent on this element: ${a.no_equivalent.join(", ")}` : ""].filter(Boolean).join("; ") }))), list("Skipped (not compatible; nothing was changed)", r.skipped.map((s) => ({ name: s.name, text: s.reason }))), list("Failed (put back exactly as it was)", r.failed.map((s) => ({ name: s.name, text: s.reason })))) }).result;
}
/** Make a copy of the copied elements, offset from the originals by the distance asked for (in the project's unit). The copies are ordinary elements. */
export async function pasteElements(store, clip) {
    if (!store.project || clip.project !== store.project.id)
        return;
    const alive = new Set((store.scene?.objects ?? []).map((o) => o.id));
    const ids = clip.ids.filter((i) => alive.has(i) || !store.scene);
    if (!ids.length) {
        toast("The copied elements are no longer in the model.", { kind: "info" });
        return;
    }
    const unit = store.unit;
    const field = (name, value) => h("input", { type: "number", value, step: "any", attrs: { "aria-label": `${name} (${unit})` } });
    const dx = field("Move X", unit === "mm" ? "1000" : unit === "cm" ? "100" : "1"), dy = field("Move Y", "0"), dz = field("Move Z", "0");
    const body = h("div", {}, h("p", { text: `Paste ${ids.length} element${ids.length === 1 ? "" : "s"} as new copies, moved from the originals by:` }), h("label", { class: "field" }, h("span", { text: `X (${unit})` }), dx), h("label", { class: "field" }, h("span", { text: `Y (${unit})` }), dy), h("label", { class: "field" }, h("span", { text: `Z (${unit})` }), dz), h("p", { class: "muted", text: "Copies are independent elements with their own ids. A copy joins the groups its original is in and follows what its original follows, so a linked set stays a linked set. A wall takes the doors and windows it hosts." }));
    const step = await openDialog({ title: "Paste elements", size: "narrow", body, actions: [{ label: "Paste", kind: "primary", run: () => {
                    const v = [Number(dx.value), Number(dy.value), Number(dz.value)];
                    if (!v.every(Number.isFinite)) {
                        toast("Enter numbers for the distance.", { kind: "info" });
                        return undefined;
                    }
                    if (v.every((n) => n === 0)) {
                        toast("Move the copy by some distance: a copy on top of its original cannot be told apart.", { kind: "info" });
                        return undefined;
                    }
                    return v;
                } }] }).result;
    if (!step)
        return;
    const out = await store.run("model.array", { ids, count: 2, step, persistent: false });
    const created = out?.result["ids"] ?? [];
    if (out && created.length) {
        await store.select({ kind: "model", ids: created, member: true });
        toast(`Pasted ${created.length} element${created.length === 1 ? "" : "s"}.`, { kind: "ok", timeoutMs: 3000 });
    }
}
/** Mirror elements across a vertical plane given by a line in the plan. The server mirrors each element by its own meaning and refuses, and leaves as it was,
 *  any element whose mirror image would be a different shape; every element is reported. */
export async function mirrorElements(store, ids, plane) {
    if (!store.project)
        return;
    const alive = new Set((store.scene?.objects ?? []).map((o) => o.id));
    const picked = ids.filter((i) => alive.has(i) || !store.scene);
    if (!picked.length) {
        toast("Select the elements to mirror first.", { kind: "info" });
        return;
    }
    let choice = plane ?? null;
    if (!choice) {
        const unit = store.unit, k = 1000 / (UNIT_MM[unit] ?? 1);
        let right = 0, found = false;
        for (const o of store.scene?.objects ?? [])
            if (picked.includes(o.id))
                for (const v of o.vertices) {
                    right = found ? Math.max(right, v[0]) : v[0];
                    found = true;
                }
        const round = (n) => String(Math.round(n * 1000) / 1000);
        const field = (name, value) => h("input", { type: "number", value, step: "any", attrs: { "aria-label": `${name} (${unit})` } });
        const kind = h("select", { attrs: { "aria-label": "Mirror plane" } }, h("option", { value: "x", text: "A vertical plane at a fixed X (left ↔ right)" }), h("option", { value: "y", text: "A vertical plane at a fixed Y (front ↔ back)" }), h("option", { value: "line", text: "A vertical plane through two points of the plan" }));
        const value = field("Position", round(right * k)), ax = field("First point X", "0"), ay = field("First point Y", "0"), bx = field("Second point X", "0"), by = field("Second point Y", "1000");
        const keep = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Keep the originals" } });
        const lineRows = h("div", {}, h("label", { class: "field" }, h("span", { text: `First point X (${unit})` }), ax), h("label", { class: "field" }, h("span", { text: `First point Y (${unit})` }), ay), h("label", { class: "field" }, h("span", { text: `Second point X (${unit})` }), bx), h("label", { class: "field" }, h("span", { text: `Second point Y (${unit})` }), by));
        const valueRow = h("label", { class: "field" }, h("span", { text: `Position (${unit})` }), value);
        const sync = () => { lineRows.hidden = kind.value !== "line"; valueRow.hidden = kind.value === "line"; };
        kind.addEventListener("change", sync);
        sync();
        const body = h("div", {}, h("p", { text: `Mirror ${picked.length} element${picked.length === 1 ? "" : "s"} across a vertical plane. A wall keeps its doors and windows (their hinges swap sides), a roof keeps its slope.` }), h("label", { class: "field" }, h("span", { text: "Plane" }), kind), valueRow, lineRows, h("label", { class: "check" }, keep, h("span", { text: "Keep the originals (mirror a copy). Untick to flip the elements themselves." })), h("p", { class: "muted", text: "A shape that is not the same when mirrored (a stair, a handed profile) is refused and left as it was; the result lists each one." }));
        choice = await openDialog({ title: "Mirror elements", size: "narrow", body, actions: [{ label: "Mirror", kind: "primary", run: () => {
                        const n = (e) => Number(e.value);
                        let p1, p2;
                        if (kind.value === "x") {
                            const v = n(value);
                            p1 = [v, 0];
                            p2 = [v, 1000];
                        }
                        else if (kind.value === "y") {
                            const v = n(value);
                            p1 = [0, v];
                            p2 = [1000, v];
                        }
                        else {
                            p1 = [n(ax), n(ay)];
                            p2 = [n(bx), n(by)];
                        }
                        if (![...p1, ...p2].every(Number.isFinite)) {
                            toast("Enter numbers for the plane.", { kind: "info" });
                            return undefined;
                        }
                        if (Math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-6) {
                            toast("The two points of the plane must be apart.", { kind: "info" });
                            return undefined;
                        }
                        return { p1, p2, copy: keep.checked };
                    } }] }).result;
    }
    if (!choice)
        return;
    const out = await store.run("model.mirror", { ids: picked, p1: choice.p1, p2: choice.p2, copy: choice.copy });
    if (!out)
        return;
    const r = out.result;
    if (r.ids.length)
        await store.select({ kind: "model", ids: r.ids, member: true });
    const verb = r.mode === "copy" ? "Mirrored copies of" : "Mirrored";
    if (!r.skipped.length && !r.failed.length && !r.applied.some((a) => a.notes.length)) {
        toast(`${verb} ${r.applied.length} element${r.applied.length === 1 ? "" : "s"}.`, { kind: "ok", timeoutMs: 3500 });
        return;
    }
    const list = (title, rows) => rows.length
        ? h("section", {}, h("h3", { text: title }), h("ul", {}, ...rows.map((x) => h("li", {}, h("strong", { text: x.name }), ` — ${x.text}`)))) : null;
    await openDialog({ title: `${verb} ${r.applied.length} element${r.applied.length === 1 ? "" : "s"}${r.skipped.length + r.failed.length ? `, ${r.skipped.length + r.failed.length} not mirrored` : ""}`, size: "wide", cancelLabel: "Close", body: h("div", {}, list("Mirrored", r.applied.map((a) => ({ name: a.name, text: a.notes.length ? a.notes.join("; ") : "done" }))), list("Skipped (left as it was)", r.skipped.map((s) => ({ name: s.name, text: s.reason }))), list("Failed (put back exactly as it was)", r.failed.map((s) => ({ name: s.name, text: s.reason })))) }).result;
}
