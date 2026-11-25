/** The View Height and View Depth fields in the status bar of a floor plan. They edit the plan's own view range (the same numbers as Properties > View range,
 *  nothing is duplicated): View Height is the cut plane above its reference level, View Depth how far below the plan's level the plan still looks. Values are
 *  typed and shown in the project's unit. The functions here are pure so they can be tested without a browser. */
import { h } from "../../../tools/web/src/dom.js";
import { UNIT_MM } from "./geom2d.js";
import type { ViewRange } from "./types.js";

export type RangeField = "height" | "depth";
/** The stored number a field edits: View Height is ``cut_offset``; View Depth is ``range_depth``, which is stored as an offset from the level (negative below it). */
export type RangeEdit = { key: "cut_offset" | "range_depth"; value: number };

const LIMIT_M = 1000;                                   // the engine's own bound on a view range number (viewrange.LIMIT)
const NUMBER = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|cm|m)?$/i;

/** Metres as the number shown in the field, in the project's unit, without trailing zeros ("1.5", "1500"). */
export function rangeText(metres: number, unit: string): string {
  const value = metres * 1000 / (UNIT_MM[unit] ?? 1);
  return String(Number(value.toFixed(4)));
}

export type RangeParse = { ok: true; value: number } | { ok: false; message: string };

/** What was typed, as a number in the project's unit. A unit may follow the number ("1.5 m"); a comma may stand for the decimal point. */
export function parseRange(text: string, unit: string): RangeParse {
  const m = NUMBER.exec(text.trim().replace(",", "."));
  if (!m) return { ok: false, message: `Type a number in ${unit}, for example ${unit === "mm" ? "1500" : "1.5"}. A unit may follow it (1.5 m).` };
  const typed = Number(m[1]);
  const factor = m[2] ? (UNIT_MM[m[2].toLowerCase()] ?? 1) / (UNIT_MM[unit] ?? 1) : 1;
  const value = typed * factor;
  if (!Number.isFinite(value)) return { ok: false, message: "That is not a number." };
  if (Math.abs(value * (UNIT_MM[unit] ?? 1)) / 1000 > LIMIT_M) return { ok: false, message: `The number is out of range (at most ${LIMIT_M} m).` };
  return { ok: true, value };
}

/** The command parameters that set a field to ``value`` (in the project's unit). The depth is typed as a distance below the level and stored as an offset. */
export function rangeEdit(field: RangeField, value: number): RangeEdit {
  return field === "height" ? { key: "cut_offset", value } : { key: "range_depth", value: value === 0 ? 0 : -value };
}

/** What the tooltip of a field says: what it is, relative to what, and how to edit it. */
export function rangeTitle(field: RangeField, range: ViewRange, unit: string): string {
  const shown = (m: number): string => `${rangeText(m, unit)} ${unit}`;
  return field === "height"
    ? `View height: the cut plane is ${shown(range.height)} above ${range.reference_name} (${shown(range.cut_z)} absolute). Type a number and press Enter; Esc cancels.`
    : `View depth: this plan looks ${shown(range.depth)} below ${range.level_name} (${shown(range.depth_z)} absolute). Lower levels inside the depth are shown in halftone and cannot be edited. Press Enter to apply; Esc cancels.`;
}

export interface RangeHost {
  /** Apply one change; resolves to whether it was accepted (the server validates the whole range and says why when it is not). */
  apply(edit: RangeEdit): Promise<boolean>;
  /** Show the plan's full view range (the Properties panel), which this field set does not repeat. */
  showAll(): void;
  /** The project's unit. */
  unit(): string;
}

/** The two fields as one element that lives as long as the status bar: it is updated, not rebuilt, so typing in it is never interrupted. */
export class ViewRangeFields {
  readonly el: HTMLElement;
  private range: ViewRange | null = null;
  private readonly inputs: Record<RangeField, HTMLInputElement>;
  private readonly units: Record<RangeField, HTMLElement>;

  constructor(private readonly host: RangeHost) {
    const field = (which: RangeField, label: string): { group: HTMLElement; input: HTMLInputElement; unit: HTMLElement } => {
      const input = h("input", { type: "text", class: "range-input", attrs: { inputmode: "decimal", autocomplete: "off", spellcheck: "false", "aria-label": label } });
      const unit = h("span", { class: "range-unit" });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); void this.commit(which).then(() => input.blur()); }
        else if (e.key === "Escape") { e.preventDefault(); this.reset(which); input.blur(); }
      });
      input.addEventListener("input", () => this.validate(which));
      input.addEventListener("blur", () => { void this.commit(which); });
      const group = h("label", { class: "range-field" }, h("span", { class: "range-label", text: label }), input, unit);
      return { group, input, unit };
    };
    const height = field("height", "View Height"), depth = field("depth", "View Depth");
    this.inputs = { height: height.input, depth: depth.input };
    this.units = { height: height.unit, depth: depth.unit };
    const all = h("button", { type: "button", class: "range-all", text: "Range…", attrs: { title: "Open the full view range (top, bottom, view depth and lower-level options) in Properties", "aria-label": "Open the full view range" }, on: { click: () => this.host.showAll() } });
    this.el = h("span", { class: "range-fields" }, height.group, depth.group, all);
    this.el.hidden = true;
  }

  /** True while a field has the keyboard, so the status bar leaves it alone until the edit is done. */
  editing(): boolean { return this.el.contains(document.activeElement); }

  /** Show ``range`` (null hides the fields: only a floor plan has a view range). A field being typed in keeps what is typed. */
  update(range: ViewRange | null): void {
    this.range = range;
    this.el.hidden = !range;
    if (!range) return;
    const unit = this.host.unit();
    for (const which of ["height", "depth"] as const) {
      const input = this.inputs[which];
      this.units[which].textContent = unit;
      input.title = rangeTitle(which, range, unit);
      if (document.activeElement !== input) input.value = rangeText(which === "height" ? range.height : range.depth, unit);
      input.removeAttribute("aria-invalid");
    }
  }

  private current(which: RangeField): string { return this.range ? rangeText(which === "height" ? this.range.height : this.range.depth, this.host.unit()) : ""; }

  private reset(which: RangeField): void {
    const input = this.inputs[which];
    input.value = this.current(which);
    input.removeAttribute("aria-invalid");
    if (this.range) input.title = rangeTitle(which, this.range, this.host.unit());
  }

  private validate(which: RangeField): RangeParse {
    const input = this.inputs[which];
    const parsed = parseRange(input.value, this.host.unit());
    if (parsed.ok) { input.removeAttribute("aria-invalid"); if (this.range) input.title = rangeTitle(which, this.range, this.host.unit()); }
    else { input.setAttribute("aria-invalid", "true"); input.title = parsed.message; }
    return parsed;
  }

  private async commit(which: RangeField): Promise<void> {
    const input = this.inputs[which];
    if (!this.range || input.value.trim() === this.current(which)) { if (this.range) this.reset(which); return; }
    const parsed = this.validate(which);
    if (!parsed.ok) { this.reset(which); return; }
    const unit = this.host.unit();
    if (rangeText(which === "height" ? this.range.height : this.range.depth, unit) === String(Number(parsed.value.toFixed(4)))) { this.reset(which); return; }
    if (!(await this.host.apply(rangeEdit(which, parsed.value)))) this.reset(which);          // refused: the server has said why; the field returns to the number in force
  }
}
