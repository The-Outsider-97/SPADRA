/** Links, chains, groups, attach and detach: the context-menu entries, the dialogs behind them and the Relationships panel.
 *
 *  The rules live on the server (``relationships.py``): this only asks, shows and reports. A change is one undoable command; a refusal comes back as the
 *  server's own message (a cycle, an element that cannot be linked, a child that would be moved twice), shown as it is. */
import { h } from "../../../tools/web/src/dom.js";
import { openDialog, toast } from "../../../tools/web/src/ui.js";
import type { MenuEntry } from "./menus.js";
import type { Store } from "./store.js";
import type { SceneObject } from "./types.js";

export interface HighlightResult { roles: Record<string, string>; edges: string[][]; groups: Array<{ id: string; name: string; parent: string | null; members: string[] }> }
interface TreeNode { id: string; name: string; inherit: string[] | null; children: TreeNode[] }
interface TreeResult {
  chains: TreeNode[]; links: number; issues: Array<{ severity: string; message: string; ref?: string }>;
  groups: Array<{ id: string; name: string; parent: string | null; members: Array<{ id: string; name: string }>; pivot: number[] | null }>;
}

const objects = (store: Store): SceneObject[] => store.scene?.objects ?? [];
const byId = (store: Store): Map<string, SceneObject> => new Map(objects(store).map((o) => [o.id, o]));
/** The name of an element; when other elements share it, where the element is (its centre in metres) is added so they can be told apart. */
const label = (store: Store, id: string): string => {
  const scene = byId(store), o = scene.get(id);
  if (!o) return id;
  let same = 0;
  for (const other of scene.values()) if (other.name === o.name && ++same > 1) break;
  if (same < 2 || !o.vertices.length) return o.name;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const v of o.vertices) for (let k = 0; k < 3; k++) { const c = v[k] ?? 0; if (c < (lo[k] ?? 0)) lo[k] = c; if (c > (hi[k] ?? 0)) hi[k] = c; }
  const mid = lo.map((l, k) => ((l + (hi[k] ?? l)) / 2).toFixed(1));
  return `${o.name} at (${mid.join(", ")}) m`;
};
const selectedIds = (store: Store): string[] => (store.selection?.kind === "model" ? [...store.selection.ids] : []);
const noun = (n: number): string => (n === 1 ? "1 element" : `${n} elements`);

/** The menu entries for the relationships of an element (the selection when the element is part of it). */
export function relationshipEntries(store: Store, id: string): MenuEntry[] {
  const picked = selectedIds(store);
  const ids = picked.includes(id) ? picked : [id];
  const scene = byId(store);
  const here = scene.get(id);
  const others = ids.filter((i) => i !== id);
  const anyParent = ids.some((i) => scene.get(i)?.parent);
  const anyGroup = ids.some((i) => scene.get(i)?.group);
  const entries: MenuEntry[] = ["separator"];
  if (others.length) entries.push({ label: `Link: ${noun(others.length)} follow this element`, icon: "link-chain", run: () => { void linkDialog(store, others, id); } });
  entries.push({ label: "Link: follow another element…", icon: "link-chain", run: () => { void linkDialog(store, ids); } });
  if (ids.length >= 2) entries.push({ label: "Chain in selection order…", icon: "link-chain", run: () => { void chainDialog(store, ids); } });
  if (anyParent) entries.push({ label: "Unlink from parent…", icon: "unlink", run: () => { void unlinkDialog(store, ids); } });
  entries.push({ label: ids.length >= 2 ? "Group" : "Group…", icon: "group", run: () => { void groupDialog(store, ids); } });
  if (anyGroup) entries.push({ label: "Ungroup", icon: "ungroup", run: () => { void ungroup(store, ids); } });
  entries.push({ label: "Attach…", icon: "attach", run: () => { void attachDialog(store, ids); } });
  if (anyParent || anyGroup) entries.push({ label: "Detach…", icon: "detach", run: () => { void detachDialog(store, ids); } });
  entries.push({ label: "Show relationships", icon: "link-chain", run: () => { void relationshipsPanel(store, here ? id : undefined); } });
  return entries;
}

function inheritBoxes(initial: string[] = ["translate", "rotate"]): { el: HTMLElement; value: () => string[] } {
  const move = h("input", { type: "checkbox", checked: initial.includes("translate"), attrs: { "aria-label": "Follows moves" } });
  const turn = h("input", { type: "checkbox", checked: initial.includes("rotate"), attrs: { "aria-label": "Follows turns" } });
  return { el: h("fieldset", {}, h("legend", { text: "The child follows its parent's" }),
    h("label", { class: "check" }, move, h("span", { text: "Moves (translation)" })), h("label", { class: "check" }, turn, h("span", { text: "Turns (rotation)" })),
    h("p", { class: "muted", text: "Scaling and changes of size never reach a child." })),
  value: () => [...(move.checked ? ["translate"] : []), ...(turn.checked ? ["rotate"] : [])] };
}

function pickList(store: Store, exclude: ReadonlySet<string>, ariaLabel: string): HTMLSelectElement {
  const groups = new Map<string, SceneObject[]>();
  for (const o of objects(store)) if (!exclude.has(o.id) && o.category !== "void") (groups.get(o.category) ?? groups.set(o.category, []).get(o.category) as SceneObject[]).push(o);
  const select = h("select", { attrs: { "aria-label": ariaLabel } });
  for (const [category, list] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    select.appendChild(h("optgroup", { attrs: { label: category } }, ...list.map((o) => ({ o, text: label(store, o.id) })).sort((a, b) => a.text.localeCompare(b.text, undefined, { numeric: true })).map(({ o, text }) => h("option", { value: o.id, text }))));
  }
  return select;
}

/** Make ``parent`` the transform-parent of ``children``: they follow it. With no parent given, the person picks one. */
export async function linkDialog(store: Store, children: string[], parent?: string): Promise<void> {
  if (!store.project) return;
  const exclude = new Set(children);
  const select = pickList(store, exclude, "Parent element");
  if (parent) select.value = parent;
  const inherit = inheritBoxes();
  const body = h("div", {},
    h("p", { text: `${noun(children.length)} will follow the element you choose. Moving or turning the parent moves them with it; moving one of them never moves the parent or the others.` }),
    h("label", { class: "field" }, h("span", { text: "Follows (the parent)" }), select), inherit.el);
  if (parent) select.disabled = true;
  const chosen = await openDialog<{ parent: string; inherit: string[] }>({ title: "Link elements", size: "narrow", body, actions: [{ label: "Link", kind: "primary", run: () => {
    if (!select.value) { toast("There is no other element to follow.", { kind: "info" }); return undefined; }
    const how = inherit.value();
    if (!how.length) { toast("Choose what the child follows: moves, turns or both.", { kind: "info" }); return undefined; }
    return { parent: select.value, inherit: how };
  } }] }).result;
  if (!chosen) return;
  const out = await store.run("rel.link", { ids: children, parent: chosen.parent, inherit: chosen.inherit });
  if (out) toast(`${noun(children.length)} now follow${children.length === 1 ? "s" : ""} ${label(store, chosen.parent)}.`, { kind: "ok", timeoutMs: 3000 });
}

/** Each element follows the one before it, in the order shown. */
export async function chainDialog(store: Store, ids: string[]): Promise<void> {
  if (!store.project) return;
  const order = [...ids];
  const list = h("ol", { class: "chain-order" });
  const redraw = (): void => {
    list.replaceChildren(...order.map((id, i) => h("li", {},
      h("span", { text: `${label(store, id)}${i === 0 ? " (first: the others follow it)" : ""}` }),
      h("button", { class: "iconbtn tiny", type: "button", text: "↑", disabled: i === 0, attrs: { "aria-label": `Move ${label(store, id)} earlier` }, on: { click: () => { const [x] = order.splice(i, 1); order.splice(i - 1, 0, x as string); redraw(); } } }),
      h("button", { class: "iconbtn tiny", type: "button", text: "↓", disabled: i === order.length - 1, attrs: { "aria-label": `Move ${label(store, id)} later` }, on: { click: () => { const [x] = order.splice(i, 1); order.splice(i + 1, 0, x as string); redraw(); } } }))));
  };
  redraw();
  const inherit = inheritBoxes();
  const ok = await openDialog<true>({ title: "Chain elements", size: "narrow",
    body: h("div", {}, h("p", { text: "Each element follows the one above it. Reorder them if needed." }), list, inherit.el),
    actions: [{ label: "Chain", kind: "primary", run: () => {
      if (!inherit.value().length) { toast("Choose what the children follow: moves, turns or both.", { kind: "info" }); return undefined; }
      return true;
    } }] }).result;
  if (!ok) return;
  const out = await store.run("rel.chain", { ids: order, inherit: inherit.value() });
  if (out) toast(`Chained ${noun(order.length)}.`, { kind: "ok", timeoutMs: 3000 });
}

function modeRadios(name: string, options: Array<[string, string]>, value: string): { el: HTMLElement; value: () => string } {
  const inputs = options.map(([v]) => h("input", { type: "radio", name, value: v, checked: v === value, attrs: { "aria-label": v } }));
  const el = h("div", { class: "radio-column" }, ...options.map(([v, text], i) => h("label", { class: "check" }, inputs[i] as HTMLInputElement, h("span", { text }))));
  return { el, value: () => inputs.find((i) => i.checked)?.value ?? value };
}

/** What happens to the children of an element that stops following its parent. */
function descendantsChoice(name: string): { el: HTMLElement; value: () => string } {
  const r = modeRadios(name, [["branch", "Its children keep following it (the branch moves away together)"], ["reparent", "Its children follow its old parent instead"],
    ["release", "Its children stop following too"]], "branch");
  return { el: h("fieldset", {}, h("legend", { text: "Elements that follow it" }), r.el), value: r.value };
}

export async function unlinkDialog(store: Store, ids: string[]): Promise<void> {
  if (!store.project) return;
  const scene = byId(store);
  const linked = ids.filter((i) => scene.get(i)?.parent);
  if (!linked.length) { toast("None of the selected elements follows another.", { kind: "info" }); return; }
  const hasKids = objects(store).some((o) => o.parent && linked.includes(o.parent));
  const choice = descendantsChoice("unlink-descendants");
  const body = h("div", {}, h("p", { text: `${noun(linked.length)} will stop following ${linked.length === 1 ? "its parent" : "their parents"}. Nothing moves.` }), hasKids ? choice.el : null);
  const mode = await openDialog<string>({ title: "Unlink", size: "narrow", body, actions: [{ label: "Unlink", kind: "primary", run: () => (hasKids ? choice.value() : "branch") }] }).result;
  if (!mode) return;
  const out = await store.run("rel.unlink", { ids: linked, descendants: mode });
  if (out) toast(`${noun(linked.length)} unlinked.`, { kind: "ok", timeoutMs: 3000 });
}

export async function groupDialog(store: Store, ids: string[]): Promise<void> {
  if (!store.project) return;
  if (ids.length < 2) { toast("Select at least two elements to group.", { kind: "info" }); return; }
  const scene = byId(store);
  const shared = new Set(ids.map((i) => scene.get(i)?.group ?? ""));
  const inside = shared.size === 1 && !shared.has("") ? [...shared][0] : undefined;
  const name = h("input", { type: "text", value: "Group", attrs: { "aria-label": "Group name", maxlength: "80" } });
  const nested = h("input", { type: "checkbox", checked: true, attrs: { "aria-label": "Inside the group they are in" } });
  const body = h("div", {}, h("label", { class: "field" }, h("span", { text: "Name" }), name),
    inside ? h("label", { class: "check" }, nested, h("span", { text: "Make it a sub-group of the group these elements are in" })) : null,
    h("p", { class: "muted", text: "Grouping keeps every element as it is (its own id, material and parameters). The group only selects, moves, turns and copies them together." }));
  const result = await openDialog<{ name: string; within?: string }>({ title: "Group", size: "narrow", body, actions: [{ label: "Group", kind: "primary", run: () => {
    const n = name.value.trim();
    if (!n) { toast("Give the group a name.", { kind: "info" }); return undefined; }
    return inside && nested.checked ? { name: n, within: inside } : { name: n };
  } }] }).result;
  if (!result) return;
  const out = await store.run("rel.group", { ids, name: result.name, ...(result.within ? { within: result.within } : {}) });
  if (out) toast(`Grouped ${noun(ids.length)} as “${result.name}”.`, { kind: "ok", timeoutMs: 3000 });
}

export async function ungroup(store: Store, ids: string[]): Promise<void> {
  const scene = byId(store);
  const groups = [...new Set(ids.map((i) => scene.get(i)?.group).filter((g): g is string => !!g))];
  if (!groups.length) { toast("None of the selected elements is in a group.", { kind: "info" }); return; }
  const out = await store.run("rel.ungroup", { groups });
  if (out) toast(groups.length === 1 ? "Ungrouped. Nothing moved or changed." : `Ungrouped ${groups.length} groups.`, { kind: "ok", timeoutMs: 3000 });
}

/** Attach to an existing group, make children of an element, or insert into a chain. Placement is never changed. */
export async function attachDialog(store: Store, ids: string[]): Promise<void> {
  if (!store.project) return;
  let tree: TreeResult | null = null;
  try { tree = await store.api.get<TreeResult>("/relationships/tree"); } catch { tree = null; }
  const groups = tree?.groups ?? [];
  const kind = modeRadios("attach-kind", [...(groups.length ? [["group", "To a group"] as [string, string]] : []), ["parent", "To an element, as its children (they follow it)"]], groups.length ? "group" : "parent");
  const groupSelect = h("select", { attrs: { "aria-label": "Group" } }, ...groups.map((g) => h("option", { value: g.id, text: `${g.name} (${g.members.length})` })));
  const parentSelect = pickList(store, new Set(ids), "Element to attach to");
  const inherit = inheritBoxes();
  const sync = (): void => { const k = kind.value(); groupSelect.hidden = k !== "group"; parentSelect.hidden = k !== "parent"; inherit.el.hidden = k !== "parent"; };
  kind.el.addEventListener("change", sync); sync();
  const result = await openDialog<{ group?: string; parent?: string; inherit?: string[] }>({ title: "Attach", size: "narrow",
    body: h("div", {}, h("p", { text: `Attach ${noun(ids.length)}. They stay where they are.` }), kind.el, groupSelect, parentSelect, inherit.el),
    actions: [{ label: "Attach", kind: "primary", run: () => {
      if (kind.value() === "group") return groupSelect.value ? { group: groupSelect.value } : undefined;
      if (!parentSelect.value) return undefined;
      const how = inherit.value();
      if (!how.length) { toast("Choose what the children follow: moves, turns or both.", { kind: "info" }); return undefined; }
      return { parent: parentSelect.value, inherit: how };
    } }] }).result;
  if (!result) return;
  const out = await store.run("rel.attach", { ids, ...result });
  if (out) toast(`Attached ${noun(ids.length)}.`, { kind: "ok", timeoutMs: 3000 });
}

/** Detach from the group, the parent or both, and say what happens to what followed. Placement is never changed. */
export async function detachDialog(store: Store, ids: string[]): Promise<void> {
  if (!store.project) return;
  const scene = byId(store);
  const inGroup = ids.some((i) => scene.get(i)?.group), linked = ids.some((i) => scene.get(i)?.parent);
  const scope = modeRadios("detach-scope", [...(inGroup ? [["group", "From its group (links stay)"] as [string, string]] : []), ...(linked ? [["link", "From its parent (group stays)"] as [string, string]] : []),
    ...(inGroup && linked ? [["both", "From both"] as [string, string]] : [])], inGroup && linked ? "both" : inGroup ? "group" : "link");
  const choice = descendantsChoice("detach-descendants");
  const hasKids = linked && objects(store).some((o) => o.parent && ids.includes(o.parent));
  const sync = (): void => { choice.el.hidden = !hasKids || scope.value() === "group"; };
  scope.el.addEventListener("change", sync); sync();
  const result = await openDialog<{ scope: string; descendants: string }>({ title: "Detach", size: "narrow",
    body: h("div", {}, h("p", { text: `Detach ${noun(ids.length)}. They stay where they are.` }), scope.el, choice.el),
    actions: [{ label: "Detach", kind: "primary", run: () => ({ scope: scope.value(), descendants: choice.value() }) }] }).result;
  if (!result) return;
  const out = await store.run("rel.detach", { ids, scope: result.scope, descendants: result.descendants });
  if (out) toast(`Detached ${noun(ids.length)}.`, { kind: "ok", timeoutMs: 3000 });
}

/** The structure of the project: chains and groups, each with a way to select it, and anything the checks found wrong. */
export async function relationshipsPanel(store: Store, focus?: string): Promise<void> {
  if (!store.project) return;
  let tree: TreeResult;
  try { tree = await store.api.get<TreeResult>("/relationships/tree"); } catch (error) { toast(error instanceof Error ? error.message : "The relationships could not be read.", { kind: "error" }); return; }
  let close: () => void = () => undefined;
  const select = (ids: string[]): void => { void store.select({ kind: "model", ids, member: true }); close(); };
  const chainItem = (n: TreeNode): HTMLElement => h("li", {},
    h("button", { class: `linklike${n.id === focus ? " current" : ""}`, type: "button", text: label(store, n.id), on: { click: () => select([n.id]) } }),
    n.inherit ? h("span", { class: "muted", text: ` follows ${n.inherit.join(" and ")}` }) : null,
    n.children.length ? h("ul", {}, ...n.children.map(chainItem)) : null);
  const body = h("div", {},
    h("section", {}, h("h3", { text: "Chains (what follows what)" }),
      tree.chains.length ? h("ul", { class: "relation-tree" }, ...tree.chains.map(chainItem)) : h("p", { class: "muted", text: "No element follows another. Select two or more and choose Link." })),
    h("section", {}, h("h3", { text: "Groups" }),
      tree.groups.length ? h("ul", { class: "relation-tree" }, ...tree.groups.map((g) => h("li", {},
        h("button", { class: "linklike", type: "button", text: `${g.name} (${g.members.length})`, on: { click: () => select(g.members.map((m) => m.id)) } }),
        g.parent ? h("span", { class: "muted", text: ` inside ${tree.groups.find((p) => p.id === g.parent)?.name ?? "another group"}` }) : null,
        h("span", { class: "muted", text: `: ${g.members.slice(0, 6).map((m) => label(store, m.id)).join(", ")}${g.members.length > 6 ? ", …" : ""}` })))) : h("p", { class: "muted", text: "No groups yet." })),
    tree.issues.length ? h("section", {}, h("h3", { text: "Problems" }), h("ul", {}, ...tree.issues.map((i) => h("li", { text: i.message })))) : null);
  const dialog = openDialog<true>({ title: "Relationships", size: "wide", cancelLabel: "Close", body });
  close = () => dialog.close(true);
}
