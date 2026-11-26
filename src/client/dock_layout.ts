/** The workspace's docking model: where every panel is, as plain data, and every operation a person can do to it.
 *
 *  One layout holds all panels (Project Browser, Properties, Element Browser and any a plug-in adds), so there is one rule for docking, stacking, floating,
 *  closing and restoring them rather than one hard-coded behaviour per panel. A panel is in exactly one place: a tab stack in a dock region (left, right, top or
 *  bottom of the modelling view), a floating window, or hidden (closed, and listed so it can be reopened).
 *
 *  The modelling view is never a drop target and never shrinks below a minimum (``fit``), so no arrangement can take the workspace away. Every operation is a
 *  pure function: it returns a new layout and leaves its argument alone (so undo of a layout change is just the previous value), and refuses what cannot be
 *  done with a ``DockError`` saying why. ``validate`` repairs whatever was saved (an unknown panel, a panel missing, a window on a monitor that is gone, a
 *  broken tree) and says what it changed, so a bad file can never leave the person without panels.
 *
 *  Nothing here touches the DOM or the project: the shell draws a layout, and a layout is workspace *preferences* - stored on the device, never in the model. */

// ------------------------------------------------------------------------------------------------------------------------------ data
export type Side = "left" | "right" | "top" | "bottom";
export const SIDES: readonly Side[] = ["left", "right", "top", "bottom"];

/** A panel the workspace can host. ``home`` is where it opens the first time and where Reset puts it back. */
export interface PanelDef { id: string; title: string; home: Side; /** order among the panels that share a home */ order?: number }

export interface Stack { kind: "stack"; id: string; panels: string[]; active: string; collapsed?: boolean }
export interface Split { kind: "split"; id: string; dir: "row" | "column"; children: DockNode[]; weights: number[] }
export type DockNode = Stack | Split;
export interface Region { size: number; collapsed: boolean; root: DockNode | null }
export interface Floating { id: string; panels: string[]; active: string; x: number; y: number; w: number; h: number; monitor?: string; native?: boolean }

export interface Layout {
  version: 1;
  regions: Record<Side, Region>;
  floating: Floating[];
  /** Closed panels, in the order they were closed: the "reopen" list. */
  hidden: string[];
  /** Where a panel last sat before it was floated or closed, so reopening puts it back. */
  from: Record<string, Side>;
  /** Counter for ids, so a layout never reuses one. */
  seq: number;
}

export type DropTarget =
  | { kind: "tab"; stack: string; index?: number }
  | { kind: "split"; stack: string; edge: Side }
  | { kind: "region"; side: Side }
  | { kind: "float"; x: number; y: number; w?: number; h?: number };

export interface Viewport { width: number; height: number }
export interface Rect { x: number; y: number; w: number; h: number; id?: string }

export class DockError extends Error {}

export const MIN_REGION = 160;           // px: a dock region never gets narrower (or shorter) than this while it is open
export const MIN_CENTER = 360;           // px: the modelling view always keeps at least this width
export const MIN_CENTER_HEIGHT = 240;
export const MIN_FLOAT = { w: 200, h: 120 };
export const MIN_WEIGHT = 0.08;          // of a split: no pane is squeezed out entirely
export const DEFAULT_FLOAT = { w: 320, h: 420 };
export const COLLAPSED_STRIP = 28;       // px that a collapsed region still occupies, so it can be opened again

const REGION_SIZE: Record<Side, number> = { left: 280, right: 300, top: 160, bottom: 200 };
const clone = <T,>(value: T): T => structuredClone(value);
const dirOf = (side: Side): "row" | "column" => (side === "left" || side === "right" ? "column" : "row");   // panels in a side region stack top to bottom
const edgeDir = (edge: Side): "row" | "column" => (edge === "left" || edge === "right" ? "row" : "column");
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

// ------------------------------------------------------------------------------------------------------------------------------ queries
export type Place =
  | { where: "stack"; stack: Stack; side: Side }
  | { where: "floating"; floating: Floating }
  | { where: "hidden" };

/** Every stack in a node, left to right and top to bottom. */
export function stacksOf(node: DockNode | null): Stack[] {
  if (!node) return [];
  return node.kind === "stack" ? [node] : node.children.flatMap(stacksOf);
}

export function allPanels(layout: Layout): string[] {
  return [...SIDES.flatMap((s) => stacksOf(layout.regions[s].root).flatMap((st) => st.panels)), ...layout.floating.flatMap((f) => f.panels), ...layout.hidden];
}

export function locate(layout: Layout, panel: string): Place | null {
  for (const side of SIDES) for (const stack of stacksOf(layout.regions[side].root)) if (stack.panels.includes(panel)) return { where: "stack", stack, side };
  const floating = layout.floating.find((f) => f.panels.includes(panel));
  if (floating) return { where: "floating", floating };
  return layout.hidden.includes(panel) ? { where: "hidden" } : null;
}

export function findStack(layout: Layout, id: string): { stack: Stack | Floating; side: Side | null } | null {
  for (const side of SIDES) for (const stack of stacksOf(layout.regions[side].root)) if (stack.id === id) return { stack, side };
  const f = layout.floating.find((x) => x.id === id);
  return f ? { stack: f, side: null } : null;
}

// ------------------------------------------------------------------------------------------------------------------------------ building
/** Ids a layout being recovered may still claim from what was saved: fresh ids skip them, so a restored layout keeps its own ids. */
let reserved: Set<string> | null = null;
const nextId = (layout: Layout, prefix: string): string => {
  let id: string;
  do id = `${prefix}${++layout.seq}`; while (reserved?.has(id));
  return id;
};
const newStack = (layout: Layout, panels: string[], active?: string): Stack => ({ kind: "stack", id: nextId(layout, "s"), panels, active: active ?? panels[0] ?? "" });

/** Remove empty stacks, fold a split with one child into that child, drop the ids of panels that are gone, and make every active tab a real tab. */
function tidy(node: DockNode | null): DockNode | null {
  if (!node) return null;
  if (node.kind === "stack") {
    if (!node.panels.length) return null;
    if (!node.panels.includes(node.active)) node.active = node.panels[0] ?? "";
    return node;
  }
  const kept: DockNode[] = [], weights: number[] = [];
  node.children.forEach((child, i) => {
    const t = tidy(child);
    if (!t) return;
    kept.push(t);
    weights.push(finite(node.weights[i]) && (node.weights[i] ?? 0) > 0 ? (node.weights[i] as number) : 1);
  });
  if (!kept.length) return null;
  if (kept.length === 1) return kept[0] ?? null;
  const total = weights.reduce((a, b) => a + b, 0);
  node.children = kept;
  node.weights = weights.map((w) => w / total);
  return node;
}

function tidyLayout(layout: Layout): void {
  for (const side of SIDES) {
    const region = layout.regions[side];
    region.root = tidy(region.root);
    if (!region.root) region.collapsed = false;
  }
  layout.floating = layout.floating.filter((f) => f.panels.length);
  for (const f of layout.floating) if (!f.panels.includes(f.active)) f.active = f.panels[0] ?? "";
}

/** The panels a person starts with: each panel tabbed into the stack of its home side (in ``order``), nothing floating, nothing hidden. */
export function defaultLayout(panels: readonly PanelDef[]): Layout {
  const layout: Layout = { version: 1, regions: { left: { size: REGION_SIZE.left, collapsed: false, root: null }, right: { size: REGION_SIZE.right, collapsed: false, root: null },
    top: { size: REGION_SIZE.top, collapsed: false, root: null }, bottom: { size: REGION_SIZE.bottom, collapsed: false, root: null } }, floating: [], hidden: [], from: {}, seq: 0 };
  for (const side of SIDES) {
    const here = panels.filter((p) => p.home === side).sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map((p) => p.id);
    if (here.length) layout.regions[side].root = newStack(layout, here);
  }
  return layout;
}

// ------------------------------------------------------------------------------------------------------------------------------ removing
/** Take a panel out of wherever it is (stack, window or the hidden list) and tidy what that leaves. Returns where it was. */
function lift(layout: Layout, panel: string): Place | null {
  const was = locate(layout, panel);
  if (!was) return null;
  if (was.where === "stack") {
    layout.from[panel] = was.side;
    const stack = findStack(layout, was.stack.id)?.stack as Stack;
    stack.panels = stack.panels.filter((p) => p !== panel);
  } else if (was.where === "floating") {
    const f = layout.floating.find((x) => x.id === was.floating.id);
    if (f) f.panels = f.panels.filter((p) => p !== panel);
  } else layout.hidden = layout.hidden.filter((p) => p !== panel);
  return was;
}

// ------------------------------------------------------------------------------------------------------------------------------ operations
const knows = (layout: Layout, panel: string): void => { if (!locate(layout, panel)) throw new DockError(`There is no panel '${panel}'`); };

/** Put a panel somewhere: as a tab, beside or above another stack, as a new stack in a dock region, or in a window of its own. */
export function place(layout: Layout, panel: string, target: DropTarget): Layout {
  knows(layout, panel);
  const next = clone(layout);
  if (target.kind === "tab" || target.kind === "split") {
    const found = findStack(next, target.stack);
    if (!found) throw new DockError("That place is no longer there");
    const alone = found.stack.panels.length === 1 && found.stack.panels[0] === panel;
    if (alone) {
      if (target.kind === "tab") return layout;                                          // dropping a panel on its own tab changes nothing
      throw new DockError("A panel cannot be split from itself");
    }
    if (target.kind === "split" && found.side === null) throw new DockError("A window holds tabs only: drop on its tabs, or dock the panel first");
  }
  const was = lift(next, panel);
  if (target.kind === "tab") {
    const found = findStack(next, target.stack);
    if (!found) throw new DockError("That place is no longer there");
    const at = target.index === undefined ? found.stack.panels.length : Math.max(0, Math.min(found.stack.panels.length, Math.round(target.index)));
    found.stack.panels.splice(at, 0, panel);
    found.stack.active = panel;
  } else if (target.kind === "split") {
    const found = findStack(next, target.stack);
    if (!found || found.side === null) throw new DockError("That place is no longer there");
    const fresh = newStack(next, [panel]);
    const region = next.regions[found.side];
    const dir = edgeDir(target.edge), before = target.edge === "left" || target.edge === "top";
    const insert = (node: DockNode): DockNode => {
      if (node.kind === "stack") {
        if (node.id !== found.stack.id) return node;
        return { kind: "split", id: nextId(next, "p"), dir, children: before ? [fresh, node] : [node, fresh], weights: [0.5, 0.5] };
      }
      const at = node.children.findIndex((c) => c.kind === "stack" && c.id === found.stack.id);
      if (at >= 0 && node.dir === dir) {                                                 // same direction: the stack gives the new one half of its share
        const w = node.weights[at] ?? 1 / node.children.length;
        node.children.splice(before ? at : at + 1, 0, fresh);
        node.weights.splice(at, 1, w / 2);
        node.weights.splice(before ? at : at + 1, 0, w / 2);
        return node;
      }
      node.children = node.children.map(insert);
      return node;
    };
    region.root = region.root ? insert(region.root) : fresh;
  } else if (target.kind === "region") {
    const region = next.regions[target.side];
    const fresh = newStack(next, [panel]);
    const dir = dirOf(target.side);
    if (!region.root) region.root = fresh;
    else if (region.root.kind === "split" && region.root.dir === dir) {
      const share = 1 / (region.root.children.length + 1);
      region.root.weights = region.root.weights.map((w) => w * (1 - share));
      region.root.children.push(fresh);
      region.root.weights.push(share);
    } else region.root = { kind: "split", id: nextId(next, "p"), dir, children: [region.root, fresh], weights: [0.5, 0.5] };
    region.collapsed = false;
  } else {
    if (!finite(target.x) || !finite(target.y)) throw new DockError("A window needs a position");
    const w = Math.max(MIN_FLOAT.w, finite(target.w) ? target.w : DEFAULT_FLOAT.w), h = Math.max(MIN_FLOAT.h, finite(target.h) ? target.h : DEFAULT_FLOAT.h);
    next.floating.push({ id: nextId(next, "f"), panels: [panel], active: panel, x: Math.round(target.x), y: Math.round(target.y), w: Math.round(w), h: Math.round(h) });
    if (was?.where === "floating") next.from[panel] = next.from[panel] ?? "right";
  }
  tidyLayout(next);
  return next;
}

/** Close a panel: it leaves the workspace and waits in the hidden list. */
export function close(layout: Layout, panel: string): Layout {
  knows(layout, panel);
  if (layout.hidden.includes(panel)) return layout;
  const next = clone(layout);
  lift(next, panel);
  next.hidden.push(panel);
  tidyLayout(next);
  return next;
}

/** Bring a closed panel back where it was (its last dock side), as a tab in the stack that is there, or as a new stack. Any panel can be reopened. */
export function reopen(layout: Layout, panel: string, panels: readonly PanelDef[]): Layout {
  knows(layout, panel);
  if (!layout.hidden.includes(panel)) return layout;
  const side = layout.from[panel] ?? panels.find((p) => p.id === panel)?.home ?? "right";
  const stack = stacksOf(layout.regions[side].root)[0];
  return place(layout, panel, stack ? { kind: "tab", stack: stack.id } : { kind: "region", side });
}

/** Send a panel back to its default home, whatever has happened to it (the "Reset this panel" command). */
export function resetPanel(layout: Layout, panel: string, panels: readonly PanelDef[]): Layout {
  knows(layout, panel);
  const def = panels.find((p) => p.id === panel);
  if (!def) throw new DockError(`There is no panel '${panel}'`);
  const stack = stacksOf(layout.regions[def.home].root).find((s) => !s.panels.includes(panel));
  const moved = place(layout, panel, stack ? { kind: "tab", stack: stack.id } : { kind: "region", side: def.home });
  return { ...moved, regions: { ...moved.regions, [def.home]: { ...moved.regions[def.home], collapsed: false } } };
}

export function activate(layout: Layout, stack: string, panel: string): Layout {
  const found = findStack(layout, stack);
  if (!found || !found.stack.panels.includes(panel)) throw new DockError("That tab is no longer there");
  if (found.stack.active === panel) return layout;
  const next = clone(layout);
  (findStack(next, stack)?.stack as Stack | Floating).active = panel;
  return next;
}

export function reorderTab(layout: Layout, stack: string, panel: string, index: number): Layout {
  const found = findStack(layout, stack);
  if (!found || !found.stack.panels.includes(panel)) throw new DockError("That tab is no longer there");
  const next = clone(layout);
  const s = findStack(next, stack)?.stack as Stack | Floating;
  s.panels = s.panels.filter((p) => p !== panel);
  s.panels.splice(Math.max(0, Math.min(s.panels.length, Math.round(index))), 0, panel);
  return next;
}

/** Move the divider after pane ``index`` of a split by ``delta`` (a fraction of the split), never squeezing either side below ``MIN_WEIGHT``. */
export function resizeSplit(layout: Layout, split: string, index: number, delta: number): Layout {
  const next = clone(layout);
  let hit: Split | null = null;
  const walk = (node: DockNode | null): void => { if (node?.kind === "split") { if (node.id === split) hit = node; node.children.forEach(walk); } };
  SIDES.forEach((s) => walk(next.regions[s].root));
  const target = hit as Split | null;
  if (!target || index < 0 || index >= target.children.length - 1) throw new DockError("That divider is no longer there");
  const a = target.weights[index] ?? 0, b = target.weights[index + 1] ?? 0;
  const moved = Math.max(MIN_WEIGHT - a, Math.min(b - MIN_WEIGHT, finite(delta) ? delta : 0));
  target.weights[index] = a + moved;
  target.weights[index + 1] = b - moved;
  return next;
}

export function setRegionSize(layout: Layout, side: Side, px: number): Layout {
  if (!finite(px)) throw new DockError("A size must be a number");
  const next = clone(layout);
  next.regions[side].size = Math.max(MIN_REGION, Math.round(px));
  return next;
}

export function toggleRegion(layout: Layout, side: Side): Layout {
  if (!layout.regions[side].root) return layout;
  const next = clone(layout);
  next.regions[side].collapsed = !next.regions[side].collapsed;
  return next;
}

export function moveFloating(layout: Layout, id: string, x: number, y: number): Layout {
  if (!layout.floating.some((f) => f.id === id)) throw new DockError("That window is no longer there");
  const next = clone(layout);
  const f = next.floating.find((w) => w.id === id) as Floating;
  f.x = Math.round(finite(x) ? x : f.x);
  f.y = Math.round(finite(y) ? y : f.y);
  return next;
}

export function resizeFloating(layout: Layout, id: string, w: number, h: number): Layout {
  if (!layout.floating.some((f) => f.id === id)) throw new DockError("That window is no longer there");
  const next = clone(layout);
  const f = next.floating.find((x) => x.id === id) as Floating;
  f.w = Math.max(MIN_FLOAT.w, Math.round(finite(w) ? w : f.w));
  f.h = Math.max(MIN_FLOAT.h, Math.round(finite(h) ? h : f.h));
  return next;
}

// ------------------------------------------------------------------------------------------------------------------------------ fitting
/** Make a layout fit a window: open regions never take the modelling view below its minimum, and every floating window is on a screen that exists.
 *  ``screens`` are the monitors that are connected (a window on one that is gone moves onto the first); with none given the viewport is the screen. */
export function fit(layout: Layout, viewport: Viewport, screens: readonly Rect[] = []): Layout {
  const next = clone(layout);
  const used = (s: Side): number => (!next.regions[s].root ? 0 : next.regions[s].collapsed ? COLLAPSED_STRIP : next.regions[s].size);
  const squeeze = (a: Side, b: Side, room: number): void => {
    for (const s of [a, b]) if (next.regions[s].size < MIN_REGION) next.regions[s].size = MIN_REGION;
    let over = used(a) + used(b) - room;
    for (const s of [a, b]) {
      if (over <= 0 || !next.regions[s].root || next.regions[s].collapsed) continue;
      const give = Math.min(over, next.regions[s].size - MIN_REGION);
      next.regions[s].size -= give;
      over -= give;
    }
  };
  squeeze("left", "right", viewport.width - MIN_CENTER);
  squeeze("top", "bottom", viewport.height - MIN_CENTER_HEIGHT);
  const home: Rect = screens[0] ?? { x: 0, y: 0, w: viewport.width, h: viewport.height };
  for (const f of next.floating) {
    const centre = { x: f.x + f.w / 2, y: f.y + f.h / 2 };
    const on = screens.length ? screens.find((s) => centre.x >= s.x && centre.x <= s.x + s.w && centre.y >= s.y && centre.y <= s.y + s.h) : home;
    const screen = on ?? home;                                                           // its monitor is gone: onto the first one
    f.w = Math.max(MIN_FLOAT.w, Math.min(f.w, screen.w));
    f.h = Math.max(MIN_FLOAT.h, Math.min(f.h, screen.h));
    f.x = Math.round(Math.max(screen.x, Math.min(f.x, screen.x + screen.w - f.w)));
    f.y = Math.round(Math.max(screen.y, Math.min(f.y, screen.y + screen.h - f.h)));
    if (!on && screens.length) delete f.monitor;
  }
  return next;
}

/** The pixels each dock region occupies, so the shell can lay the modelling view out in what is left. */
export function regionSizes(layout: Layout): Record<Side, number> {
  const out = {} as Record<Side, number>;
  for (const s of SIDES) out[s] = !layout.regions[s].root ? 0 : layout.regions[s].collapsed ? COLLAPSED_STRIP : layout.regions[s].size;
  return out;
}

// ------------------------------------------------------------------------------------------------------------------------------ checking
/** What is wrong with a layout (empty when it is sound): every panel exactly once, no empty stack, no one-child split, weights that add up, tabs that exist. */
export function problems(layout: Layout, panels: readonly PanelDef[]): string[] {
  const out: string[] = [];
  const seen = new Map<string, number>();
  for (const p of allPanels(layout)) seen.set(p, (seen.get(p) ?? 0) + 1);
  for (const p of panels) if (!seen.has(p.id)) out.push(`'${p.id}' is nowhere`);
  for (const [p, n] of seen) { if (n > 1) out.push(`'${p}' is in ${n} places`); if (!panels.some((d) => d.id === p)) out.push(`'${p}' is not a known panel`); }
  const check = (node: DockNode | null, where: string): void => {
    if (!node) return;
    if (node.kind === "stack") {
      if (!node.panels.length) out.push(`${where}: an empty stack`);
      if (node.panels.length && !node.panels.includes(node.active)) out.push(`${where}: the active tab is not in its stack`);
      return;
    }
    if (node.children.length < 2) out.push(`${where}: a split with ${node.children.length} pane(s)`);
    if (node.weights.length !== node.children.length) out.push(`${where}: weights and panes differ`);
    else if (Math.abs(node.weights.reduce((a, b) => a + b, 0) - 1) > 1e-6 || node.weights.some((w) => !(w > 0))) out.push(`${where}: weights do not add up`);
    node.children.forEach((c) => check(c, where));
  };
  for (const s of SIDES) check(layout.regions[s].root, s);
  for (const f of layout.floating) if (!f.panels.length || !f.panels.includes(f.active)) out.push(`window ${f.id} is empty or has no active tab`);
  return out;
}

// ------------------------------------------------------------------------------------------------------------------------------ saving and recovering
export interface Recovered { layout: Layout; repairs: string[] }

const ID = /^([spf])(\d+)$/;
/** The id a saved node had, when it is well formed and not yet taken; otherwise a fresh one. */
const claim = (raw: unknown, prefix: string, layout: Layout, claimed: Set<string>): string => {
  const id = (raw as { id?: unknown } | null)?.id;
  const m = typeof id === "string" ? ID.exec(id) : null;
  if (typeof id === "string" && m && m[1] === prefix && !claimed.has(id)) { claimed.add(id); layout.seq = Math.max(layout.seq, Number(m[2])); return id; }
  return nextId(layout, prefix);
};

const MAX_DEPTH = 12;
const collectIds = (raw: unknown, into: Set<string>, depth = 0): void => {
  const r = raw as { id?: unknown; children?: unknown } | null;
  if (!r || typeof r !== "object" || depth > MAX_DEPTH) return;
  if (typeof r.id === "string" && ID.test(r.id)) into.add(r.id);
  if (Array.isArray(r.children)) r.children.forEach((c) => collectIds(c, into, depth + 1));
};

interface Recovery { known: Set<string>; used: Set<string>; layout: Layout; claimed: Set<string> }

const asStack = (raw: unknown, cx: Recovery, claimId = true): Stack | null => {
  const r = raw as Partial<Stack> | null;
  if (!r || typeof r !== "object" || !Array.isArray(r.panels)) return null;
  const panels: string[] = [];
  for (const p of r.panels) if (typeof p === "string" && cx.known.has(p) && !cx.used.has(p)) { cx.used.add(p); panels.push(p); }   // each panel once, here and everywhere
  if (!panels.length) return null;
  const active = typeof r.active === "string" && panels.includes(r.active) ? r.active : panels[0];
  const s: Stack = { kind: "stack", id: claimId ? claim(r, "s", cx.layout, cx.claimed) : "", panels, active: active ?? "" };
  if (r.collapsed === true) s.collapsed = true;
  return s;
};

const asNode = (raw: unknown, cx: Recovery, depth = 0): DockNode | null => {
  const r = raw as { kind?: unknown; children?: unknown; dir?: unknown; weights?: unknown } | null;
  if (!r || typeof r !== "object" || depth > MAX_DEPTH) return null;
  if (r.kind === "stack") return asStack(r, cx);
  if (r.kind !== "split" || !Array.isArray(r.children)) return null;
  const kids: DockNode[] = [], weights: number[] = [];
  r.children.forEach((c, i) => {
    const n = asNode(c, cx, depth + 1);
    if (!n) return;
    kids.push(n);
    const w = Array.isArray(r.weights) ? r.weights[i] : undefined;
    weights.push(finite(w) && w > 0 ? w : 1);
  });
  if (!kids.length) return null;
  if (kids.length === 1) return kids[0] ?? null;
  const total = weights.reduce((a, b) => a + b, 0);
  return { kind: "split", id: claim(r, "p", cx.layout, cx.claimed), dir: r.dir === "column" ? "column" : "row", children: kids, weights: weights.map((w) => w / total) };
};

/** Turn whatever was stored into a sound layout for these panels, and report what had to change. Anything unusable gives the default layout. */
export function validate(raw: unknown, panels: readonly PanelDef[], viewport?: Viewport, screens: readonly Rect[] = []): Recovered {
  const repairs: string[] = [];
  const saved = raw as Partial<Layout> | null;
  if (!saved || typeof saved !== "object" || saved.version !== 1 || !saved.regions || typeof saved.regions !== "object") {
    if (raw !== null && raw !== undefined) repairs.push("The saved workspace could not be read: the default one is used");
    return { layout: defaultLayout(panels), repairs };
  }
  const ids = new Set<string>();
  for (const side of SIDES) collectIds((saved.regions as Record<string, unknown>)[side] && ((saved.regions as Record<string, { root?: unknown }>)[side] ?? {}).root, ids);
  if (Array.isArray(saved.floating)) for (const f of saved.floating) collectIds(f, ids);
  const previous = reserved;
  reserved = ids;
  try {
    const layout = defaultLayout([]);
    const cx: Recovery = { known: new Set(panels.map((p) => p.id)), used: new Set<string>(), layout, claimed: new Set<string>() };
    for (const side of SIDES) {
      const r = (saved.regions as Record<string, Partial<Region> | null | undefined>)[side];
      layout.regions[side].size = finite(r?.size) && r.size >= MIN_REGION ? Math.round(r.size) : REGION_SIZE[side];
      layout.regions[side].collapsed = r?.collapsed === true;
      layout.regions[side].root = r?.root ? asNode(r.root, cx) : null;
      if (r?.root && !layout.regions[side].root) repairs.push(`The ${side} dock held nothing that exists any more and was cleared`);
    }
    for (const entry of Array.isArray(saved.floating) ? saved.floating : []) {
      const f = entry as Partial<Floating> | null;
      const s = asStack({ panels: f?.panels, active: f?.active }, cx, false);
      if (!s || !f) { repairs.push("A floating window with no usable panel was dropped"); continue; }
      layout.floating.push({ id: claim(f, "f", layout, cx.claimed), panels: s.panels, active: s.active, x: finite(f.x) ? Math.round(f.x) : 80, y: finite(f.y) ? Math.round(f.y) : 80,
        w: finite(f.w) ? Math.round(f.w) : DEFAULT_FLOAT.w, h: finite(f.h) ? Math.round(f.h) : DEFAULT_FLOAT.h, ...(typeof f.monitor === "string" ? { monitor: f.monitor } : {}),
        ...(f.native === true ? { native: true } : {}) });
    }
    for (const p of Array.isArray(saved.hidden) ? saved.hidden : []) if (typeof p === "string" && cx.known.has(p) && !cx.used.has(p)) { cx.used.add(p); layout.hidden.push(p); }
    for (const [p, side] of Object.entries(saved.from ?? {})) if (cx.known.has(p) && (SIDES as readonly string[]).includes(side as string)) layout.from[p] = side as Side;
    if (finite(saved.seq) && saved.seq > layout.seq) layout.seq = Math.floor(saved.seq);
    tidyLayout(layout);
    let out = layout;
    for (const def of panels) {                                                            // a panel the saved layout does not know (new, or added by a plug-in) is brought in
      if (cx.used.has(def.id)) continue;
      repairs.push(`'${def.title}' was not in the saved workspace and was added to the ${def.home} dock`);
      const stack = stacksOf(out.regions[def.home].root)[0];
      out = place({ ...out, hidden: [...out.hidden, def.id] }, def.id, stack ? { kind: "tab", stack: stack.id } : { kind: "region", side: def.home });
      cx.used.add(def.id);
    }
    const before = JSON.stringify(out.floating.map((f) => [f.x, f.y, f.w, f.h]));
    if (viewport) {
      out = fit(out, viewport, screens);
      if (JSON.stringify(out.floating.map((f) => [f.x, f.y, f.w, f.h])) !== before) repairs.push("A floating panel was off the screen (a monitor may be disconnected) and was moved back");
    }
    return { layout: out, repairs };
  } finally {
    reserved = previous;
  }
}

export const serialize = (layout: Layout): string => JSON.stringify(layout);

export function parse(text: string | null, panels: readonly PanelDef[], viewport?: Viewport, screens: readonly Rect[] = []): Recovered {
  if (text === null) return { layout: defaultLayout(panels), repairs: [] };
  try { return validate(JSON.parse(text) as unknown, panels, viewport, screens); } catch { return { layout: defaultLayout(panels), repairs: ["The saved workspace could not be read: the default one is used"] }; }
}

// ------------------------------------------------------------------------------------------------------------------------------ the saved workspaces
export interface KeyStore { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
export const CURRENT_KEY = "spadra.workspace.current";
export const NAMED_PREFIX = "spadra.workspace.named.";

/** The workspace on this device: the layout in use and any the person named. It is a preference of the device (never part of a project), so a storage that
 *  is unavailable only means the arrangement lasts for this session. */
export class WorkspaceStore {
  constructor(private readonly storage: KeyStore | null, private readonly panels: () => readonly PanelDef[]) {}
  private read(key: string): string | null { try { return this.storage?.getItem(key) ?? null; } catch { return null; } }
  private write(key: string, value: string | null): void { try { if (value === null) this.storage?.removeItem(key); else this.storage?.setItem(key, value); } catch { /* session only */ } }
  current(viewport?: Viewport, screens: readonly Rect[] = []): Recovered { return parse(this.read(CURRENT_KEY), this.panels(), viewport, screens); }
  save(layout: Layout): void { this.write(CURRENT_KEY, serialize(layout)); }
  saveNamed(name: string, layout: Layout): void {
    const n = name.trim();
    if (!n || n.length > 60) throw new DockError("Give the workspace a name of 1 to 60 characters");
    this.write(NAMED_PREFIX + n, serialize(layout));
  }
  names(): string[] {
    const out: string[] = [];
    try { const s = this.storage as (KeyStore & { length?: number; key?(i: number): string | null }) | null; for (let i = 0; i < (s?.length ?? 0); i++) { const k = s?.key?.(i); if (k?.startsWith(NAMED_PREFIX)) out.push(k.slice(NAMED_PREFIX.length)); } } catch { /* none */ }
    return out.sort((a, b) => a.localeCompare(b));
  }
  loadNamed(name: string, viewport?: Viewport, screens: readonly Rect[] = []): Recovered {
    const text = this.read(NAMED_PREFIX + name);
    if (text === null) throw new DockError(`There is no saved workspace called '${name}'`);
    return parse(text, this.panels(), viewport, screens);
  }
  removeNamed(name: string): void { this.write(NAMED_PREFIX + name, null); }
  /** The default arrangement (the "Restore default workspace" command); it replaces the one in use. */
  restoreDefault(): Layout { const layout = defaultLayout(this.panels()); this.save(layout); return layout; }
}
