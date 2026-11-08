/** The Project Browser: the server's project tree (views, models, drawings, levels, families, categories, sheets, scenes, animation assets, reports), with search,
 *  keyboard navigation, and the row for the current selection revealed. What a row does is stated by the server: open a view, select an element,
 *  open a dialog, or show a report. */
import { h } from "../../../tools/web/src/dom.js";
import { glyph } from "../../../tools/web/src/icons.js";
import { confirmDialog } from "../../../tools/web/src/ui.js";
import { openMenu, type MenuEntry } from "./menus.js";
import { flatten, type Store } from "./store.js";
import type { TreeNode } from "./types.js";

const ICONS: Record<string, string> = {
  views: "views", "views-3d": "cube", "views-plan": "plan", "views-section": "section", "views-elevation": "elevation", "views-schedule": "table",
  models: "cube", drawings: "plan", levels: "levels", families: "family", sheets: "page", scenes: "clock", "animation-assets": "layers", reports: "report", categories: "list",
  "cat-Walls": "wall", "cat-Doors": "door", "cat-Windows": "window", "cat-Floors": "floor", "cat-Roofs": "roof", "cat-Gutters": "gutter", "cat-Downpipes": "downpipe", "cat-Structural": "beam", "cat-Rooms": "room",
  "cat-Columns": "column", "cat-Stairs": "stair", "cat-Grids": "grid", "cat-Generic": "family", "cat-Extrusions": "extrude", "cat-Voids": "void", "cat-Sweeps": "sweep", "cat-Foundations": "foundation",
};

/** The icon for a row: its own entry, else what it opens, else what it is. */
export function iconFor(n: TreeNode): string {
  const fromId = ICONS[n.id];
  if (fromId) return fromId;
  if (n.report) return "report";
  if (n.dialog) return "family";
  if (n.id.startsWith("family-")) return "family";
  if (n.open?.kind === "3d") return "cube";
  if (n.open?.kind === "sheet") return "page";
  if (n.open) return n.view_kind === "section" ? "section" : n.view_kind === "elevation" ? "elevation" : n.view_kind === "schedule" ? "table" : "plan";
  if (n.id.startsWith("track-")) return "clock";
  if (n.id.startsWith("marker-")) return "flag";
  return n.select ? "wall" : "layout";
}

/** Ids of the groups that contain the row, outermost first; empty when it is not in the tree. */
export function ancestors(tree: TreeNode[], id: string): string[] {
  const walk = (nodes: TreeNode[], path: string[]): string[] | null => {
    for (const n of nodes) {
      if (n.id === id) return path;
      const found = n.children ? walk(n.children, [...path, n.id]) : null;
      if (found) return found;
    }
    return null;
  };
  return walk(tree, []) ?? [];
}

export class Browser {
  readonly el: HTMLElement;
  private readonly list: HTMLElement;
  private readonly search: HTMLInputElement;
  private readonly collapsed = new Set<string>();
  private readonly seen = new Set<string>();
  private revealedFor: string | null = null;

  constructor(private readonly store: Store) {
    this.search = h("input", { type: "search", placeholder: "Search…", attrs: { "aria-label": "Search the project", autocomplete: "off" }, on: { input: () => this.update() } });
    this.list = h("div", { class: "panel-body", attrs: { role: "tree", "aria-label": "Project Browser" }, on: { keydown: (e) => this.key(e) } });
    this.el = h("section", { class: "panel", attrs: { "aria-label": "Project Browser" } },
      h("div", { class: "panel-head" }, h("span", { text: "Project Browser" })),
      h("div", { class: "search" }, glyph("search"), this.search), this.list);
  }

  /** Put the search box in reach from anywhere (the "/" shortcut). */
  focusSearch(): void { this.search.focus(); this.search.select(); }

  update(): void {
    const query = this.search.value.trim().toLowerCase();
    // Groups start open except the long ones (an element category, a family's instances); after that the person's choices are kept.
    for (const n of flatten(this.store.tree)) {
      if (!this.seen.has(n.id)) { this.seen.add(n.id); if (n.id.startsWith("cat-") || (n.id.startsWith("family-") && (n.children?.length ?? 0) > 8)) this.collapsed.add(n.id); }
    }
    this.reveal();
    const hadFocus = this.list.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset["node"] : undefined;
    const ul = h("ul", { class: "tree", attrs: { role: "group" } });
    for (const n of this.store.tree) { const el = this.node(n, query, 0); if (el) ul.appendChild(el); }
    while (this.list.firstChild) this.list.removeChild(this.list.firstChild);
    this.list.appendChild(ul);
    if (!ul.children.length) this.list.appendChild(h("p", { class: "note", text: query ? "Nothing matches that search." : "The project is empty." }));
    if (hadFocus) this.list.querySelector<HTMLElement>(`[data-node="${CSS.escape(hadFocus)}"]`)?.focus();
    this.list.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
  }

  /** When the selection changes to an element, open the groups that hold its row so it can be seen (once per selection, so the person can still close them). */
  private reveal(): void {
    const id = this.store.selectedNodeId();
    const key = id ? `${id}@${this.store.selection?.kind === "model" ? this.store.selection.ids.join(",") : ""}` : null;
    if (key === this.revealedFor) return;
    this.revealedFor = key;
    if (id) for (const a of ancestors(this.store.tree, id)) this.collapsed.delete(a);
  }

  private matches(n: TreeNode, query: string): boolean {
    return n.label.toLowerCase().includes(query) || (n.detail ?? "").toLowerCase().includes(query) || (n.children ?? []).some((c) => this.matches(c, query));
  }

  private isCurrent(n: TreeNode): boolean {
    const tab = this.store.tab, sel = this.store.selection;
    if (n.open && !n.id.startsWith("scene-") && tab && tab.ref.id === n.open.id && !n.id.startsWith("model-") && !n.id.startsWith("drawing-")) return true;
    return (sel?.kind === "level" && sel.id === n.id) ||
      !!(n.select && sel?.kind === "model" && sel.ids.includes(n.select.model));
  }

  private node(n: TreeNode, query: string, depth: number): HTMLElement | null {
    if (query && !this.matches(n, query)) return null;
    const kids = n.children ?? [];
    const expandable = kids.length > 0;
    const open = query ? true : !this.collapsed.has(n.id);
    const current = this.isCurrent(n);
    const row: HTMLButtonElement = h("button", {
      class: "node-row", type: "button", data: { node: n.id, flag: n.flag ?? undefined },
      attrs: { role: "treeitem", "aria-level": String(depth + 1), "aria-current": current ? "true" : undefined, "aria-selected": String(current), tabindex: depth === 0 && n === this.store.tree[0] ? "0" : "-1",
        ...(n.flag === "changed" ? { title: "This sheet differs from its last issue" } : {}), ...(expandable ? { "aria-expanded": String(open) } : {}) },
      on: { click: () => { void this.activate(n, expandable); }, contextmenu: (e) => { e.preventDefault(); this.menuFor(n, row); } },
    }, h("span", { class: "twisty", on: { click: (e) => { if (expandable) { e.stopPropagation(); this.toggle(n.id); } } } }, expandable ? glyph("chevron-down") : null), glyph(iconFor(n)), h("span", { class: "node-label", text: n.label }),
      n.detail ? h("span", { class: "detail", text: n.detail }) : null, n.flag === "changed" ? h("span", { class: "flag", attrs: { "aria-hidden": "true" }, text: "●" }) : null);
    const li = h("li", { class: expandable && depth < 2 ? "node-group" : "", attrs: { role: "none" } }, row);
    if (expandable && open) {
      const ul = h("ul", { attrs: { role: "group" } });
      for (const k of kids) { const el = this.node(k, query, depth + 1); if (el) ul.appendChild(el); }
      li.appendChild(ul);
    }
    return li;
  }

  /** Arrow keys move between rows, Right/Left open and close a group, Home/End go to the ends: a tree is operable without a mouse. */
  private key(e: KeyboardEvent): void {
    const rows = [...this.list.querySelectorAll<HTMLButtonElement>(".node-row")];
    const at = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    const row = rows[at] as HTMLButtonElement;
    const go = (i: number): void => { e.preventDefault(); rows[Math.max(0, Math.min(rows.length - 1, i))]?.focus(); };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(rows.length - 1);
    else if (e.key === "ArrowRight" && row.getAttribute("aria-expanded") === "false") { e.preventDefault(); this.toggle(row.dataset["node"] ?? ""); }
    else if (e.key === "ArrowRight" && row.getAttribute("aria-expanded") === "true") go(at + 1);
    else if (e.key === "ArrowLeft" && row.getAttribute("aria-expanded") === "true") { e.preventDefault(); this.toggle(row.dataset["node"] ?? ""); }
    else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) { e.preventDefault(); const n = flatten(this.store.tree).find((t) => t.id === row.dataset["node"]); if (n) this.menuFor(n, row); }
    else if (e.key === "ArrowLeft") { const level = Number(row.getAttribute("aria-level")); for (let i = at - 1; i >= 0; i--) if (Number(rows[i]?.getAttribute("aria-level")) < level) { go(i); break; } }
  }

  /** What can be done with a row: open it, show its properties, delete it (when the server has a command that deletes that kind of row). */
  private menuFor(n: TreeNode, anchor: HTMLElement): void {
    const entries: MenuEntry[] = [{ label: n.select ? "Select" : "Open", icon: iconFor(n), disabled: !(n.report || n.dialog || n.open || n.select || this.store.levels.some((level) => level.id === n.id)), run: () => { void this.activate(n, false); } }];
    const del = this.deletion(n);
    if (del) entries.push("separator", { label: `Delete ${n.label}`, icon: "close", run: () => { void this.confirmDelete(n, del); } });
    openMenu(anchor, entries, { label: n.label });
  }

  /** The command that deletes this row, if one exists and the row is not a built-in (the 3D model, a level's plan). */
  private deletion(n: TreeNode): { op: string; params: Record<string, unknown>; what: string } | null {
    if (n.report) return { op: "report.delete", params: { id: n.report }, what: "report" };
    if (n.id.startsWith("family-") && n.ref) return { op: "family.delete", params: { id: n.ref }, what: "family" };
    if (n.select) return { op: "model.delete", params: { id: n.select.model }, what: "element" };
    if (n.open?.kind === "sheet") return { op: "sheet.delete", params: { id: n.open.id }, what: "sheet" };
    if (n.open?.kind === "drawing" && (n.view_kind === "section" || n.view_kind === "elevation")) return { op: "view.delete", params: { id: n.open.id }, what: "view" };
    if (n.open?.kind === "schedule") return { op: "view.delete", params: { id: n.open.id }, what: "schedule" };
    return null;
  }

  private async confirmDelete(n: TreeNode, del: { op: string; params: Record<string, unknown>; what: string }): Promise<void> {
    const issued = del.op === "sheet.delete" && this.store.sync?.sheets.some((sh) => sh.id === del.params["id"] && sh.state !== "not issued");
    const ok = await confirmDialog({ title: `Delete ${del.what}`, message: `Delete “${n.label}”${issued ? " together with its issued revisions" : ""}? Undo brings it back.`, confirmLabel: "Delete", danger: true });
    if (ok) await this.store.run(del.op, issued ? { ...del.params, confirm: true } : del.params);
  }

  private toggle(id: string): void {
    if (this.collapsed.has(id)) this.collapsed.delete(id); else this.collapsed.add(id);
    this.update();
  }

  private async activate(n: TreeNode, expandable: boolean): Promise<void> {
    if (n.report) { this.store.openDialog?.("report", n.report); return; }
    if (n.dialog) { this.store.openDialog?.(n.dialog, n.ref); return; }
    // A level opens its plan and remains selected in Properties, including
    // legacy roof levels whose plan is created on demand.
    if (this.store.levels.some((level) => level.id === n.id)) {
      if (n.open) await this.store.openTab(n.open, n.label);
      else {
        const made = await this.store.run("level.ensure_plan", { id: n.id });
        if (typeof made?.result["id"] === "string") await this.store.openTab({ kind: "drawing", id: made.result["id"] }, String(made.result["name"] ?? n.label));
      }
      await this.store.select({ kind: "level", id: n.id });
      return;
    }
    if (n.open) { await this.store.openTab(n.open, n.label); return; }
    if (n.select) {
      const model = flatten(this.store.tree).find((t) => t.open?.kind === "3d");
      if (this.store.tab?.ref.kind !== "3d" && model?.open) await this.store.openTab(model.open, model.label);
      await this.store.select({ kind: "model", ids: [n.select.model] });
      return;
    }
    if (expandable) this.toggle(n.id);
  }
}
