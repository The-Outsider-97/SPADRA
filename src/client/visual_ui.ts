/** The controls for how a 3D view is drawn: the mode, and only what that mode uses (edge thickness, shadows, sun). One component serves the 3D view (its
 *  settings are saved with the view) and the snapshot dialog (its settings are a temporary copy), so the two cannot offer different things.
 *  The elements are built once and updated in place: a slider being dragged is never rebuilt under the pointer. */
import { h } from "../../../tools/web/src/dom.js";
import {
  controlsFor, EDGE_BOOST_RANGE, edgeFactor, MODE_HINT, MODE_LABEL, MODES, SUN_ELEVATION_RANGE, shadowsOn, type Mode, type ShadowMode, type Visual, type VisualPatch,
} from "./visual_shading.js";

export interface VisualHost {
  get(): Visual;
  /** A change that is final (a click, a slider released, a number typed). */
  commit(patch: VisualPatch): void;
  /** A change on its way (a slider being dragged): show it, do not save it. Optional; without it a slider commits as it moves. */
  preview?(patch: VisualPatch): void;
}

export class VisualControls {
  readonly el: HTMLElement;
  private readonly modeButtons = new Map<Mode, HTMLButtonElement>();
  private readonly edge: { row: HTMLElement; range: HTMLInputElement; out: HTMLElement };
  private readonly shadows: { row: HTMLElement; box: HTMLInputElement };
  private readonly sun: { row: HTMLElement; az: HTMLInputElement; azOut: HTMLElement; el: HTMLInputElement; elOut: HTMLElement };

  constructor(private readonly host: VisualHost, options: { label?: string } = {}) {
    const modes = h("div", { class: "visual-modes", attrs: { role: "group", "aria-label": options.label ?? "Visualization mode" } },
      ...MODES.map((mode) => {
        const button = h("button", { type: "button", class: "visual-mode", text: MODE_LABEL[mode], data: { mode }, attrs: { title: MODE_HINT[mode], "aria-pressed": "false" },
          on: { click: () => { if (this.host.get().mode !== mode) this.host.commit({ mode }); } } });
        this.modeButtons.set(mode, button);
        return button;
      }));

    const slider = (label: string, min: number, max: number, step: number, patch: (n: number) => VisualPatch): { range: HTMLInputElement; out: HTMLElement; row: HTMLElement } => {
      const range = h("input", { type: "range", class: "visual-range", attrs: { min: String(min), max: String(max), step: String(step), "aria-label": label } });
      const out = h("output", { class: "visual-out" });
      range.addEventListener("input", () => { const value = Number(range.value); if (this.host.preview) this.host.preview(patch(value)); else this.host.commit(patch(value)); });
      range.addEventListener("change", () => this.host.commit(patch(Number(range.value))));
      return { range, out, row: h("label", { class: "visual-row" }, h("span", { class: "visual-label", text: label }), range, out) };
    };
    const thickness = slider("Edge thickness", EDGE_BOOST_RANGE[0], EDGE_BOOST_RANGE[1], 10, (n) => ({ edge_boost: n }));
    this.edge = { row: thickness.row, range: thickness.range, out: thickness.out };

    const box = h("input", { type: "checkbox", attrs: { "aria-label": "Shadows" } });
    box.addEventListener("change", () => { const mode = this.host.get().mode; if (mode === "lined" || mode === "colored" || mode === "gray") this.host.commit({ shadows: { [mode as ShadowMode]: box.checked } }); });
    this.shadows = { row: h("label", { class: "visual-row visual-check" }, box, h("span", { class: "visual-label", text: "Shadows" })), box };

    const az = slider("Sun direction", 0, 360, 1, (n) => ({ sun: { azimuth: n % 360 } }));
    const el = slider("Sun height", SUN_ELEVATION_RANGE[0], SUN_ELEVATION_RANGE[1], 1, (n) => ({ sun: { elevation: n } }));
    az.range.title = "Where the sun is, clockwise from north: 90° is east, 180° south, 270° west.";
    el.range.title = "How high the sun stands above the horizon: 90° is straight overhead.";
    this.sun = { row: h("div", { class: "visual-sun" }, az.row, el.row), az: az.range, azOut: az.out, el: el.range, elOut: el.out };

    this.el = h("div", { class: "visual-controls" }, modes, this.edge.row, this.shadows.row, this.sun.row);
    this.update();
  }

  /** Show the host's current settings: the pressed mode, the values, and only the controls the mode uses. */
  update(): void {
    const v = this.host.get(), c = controlsFor(v);
    for (const [mode, button] of this.modeButtons) button.setAttribute("aria-pressed", String(mode === v.mode));
    this.edge.row.hidden = !c.edgeThickness;
    this.edge.range.value = String(v.edge_boost);
    this.edge.out.textContent = `×${edgeFactor(v.edge_boost).toFixed(1)}`;
    this.edge.range.setAttribute("aria-valuetext", `${v.edge_boost} percent thicker, ${edgeFactor(v.edge_boost).toFixed(1)} times`);
    this.shadows.row.hidden = !c.shadowChoice;
    this.shadows.box.checked = v.mode === "lined" || v.mode === "colored" || v.mode === "gray" ? v.shadows[v.mode] : false;
    this.sun.row.hidden = !c.sun;
    this.sun.az.value = String(v.sun.azimuth); this.sun.azOut.textContent = `${Math.round(v.sun.azimuth)}°`;
    this.sun.el.value = String(v.sun.elevation); this.sun.elOut.textContent = `${Math.round(v.sun.elevation)}°`;
    this.el.dataset["mode"] = v.mode;
    this.el.dataset["shadows"] = String(shadowsOn(v));
  }
}
