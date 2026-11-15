import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import vm from "node:vm";

const app = join(import.meta.dirname, "../src/client");
const loader = join(import.meta.dirname, "../../tools/web/startup/loader.js");

class Node {
  children: Node[] = [];
  attrs = new Map<string, string>();
  classes = new Set<string>();
  classList = { add: (name: string) => this.classes.add(name), contains: (name: string) => this.classes.has(name) };
  textContent = "";
  max?: number;
  value?: number;
  constructor(readonly tag: string) {}
  set className(value: string) { this.classes = new Set(value.split(/\s+/)); }
  get className(): string { return [...this.classes].join(" "); }
  setAttribute(key: string, value: string): void { this.attrs.set(key, value); }
  getAttribute(key: string): string | null { return this.attrs.get(key) ?? null; }
  addEventListener(): void {}
  showModal(): void { this.attrs.set("open", ""); }
  close(): void { this.attrs.delete("open"); }
  remove(): void { this.attrs.set("removed", "true"); }
  append(...items: Node[]): void { this.children.push(...items); }
  querySelector(selector: string): Node | null {
    const match = (node: Node): boolean => selector === "progress" || selector === "button" ? node.tag === selector : false;
    for (const node of this.children) {
      if (match(node)) return node;
      const nested = node.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  }
}

test("SPADRA startup preserves the shared loader and creates the requested composition", () => {
  const html = readFileSync(join(app, "index.html"), "utf8");
  assert.match(html, /data-variant="spadra"/);
  assert.doesNotMatch(html, /<script\s*>/, "CSP forbids inline startup scripts");
  const script = readFileSync(loader, "utf8");
  const body = new Node("body");
  const events = new Map<string, Set<(event: { detail?: { progress?: number } }) => void>>();
  const window = {
    location: { href: "http://localhost/", origin: "http://localhost" },
    addEventListener(type: string, fn: (event: { detail?: { progress?: number } }) => void) {
      if (!events.has(type)) events.set(type, new Set());
      events.get(type)!.add(fn);
    },
    removeEventListener(type: string, fn: (event: { detail?: { progress?: number } }) => void) {
      events.get(type)?.delete(fn);
    },
    dispatch(type: string, event: { detail?: { progress?: number } } = {}) {
      for (const fn of [...(events.get(type) ?? [])]) fn(event);
    },
  };
  const document = {
    body,
    currentScript: { dataset: { name: "SPADRA", logo: "/assets/img/spadra_main.png", variant: "spadra",
      subtitle: "Spatial Parametric Authoring, Design & Engineering" } },
    createElement: (tag: string) => new Node(tag),
  };
  vm.runInNewContext(script, { document, window, Number, Image: class extends Node { constructor() { super("img"); } },
    AbortController, setTimeout: () => 1, clearTimeout: () => {} });
  const panel = body.children[0];
  assert.ok(panel.classes.has("spadra-startup"));
  const content = panel.children[0];
  assert.deepEqual(content.children.map(n => n.tag), ["div", "p", "progress", "h1", "p"]);
  const status = content.children[1];
  assert.equal(status.textContent, "Loading ...");
  assert.equal(content.children[3].textContent, "SPADRA");
  assert.equal(content.children[4].textContent, "Spatial Parametric Authoring, Design & Engineering");
  const progress = content.children[2];
  assert.equal(progress.value, undefined, "no invented progress value during initialization");
  window.dispatch("slai:startup-update", { detail: { progress: 0.45 } });
  assert.equal(progress.value, 0.45);
  window.dispatch("slai:startup-update", { detail: { progress: 5 } });
  assert.equal(progress.value, 0.45, "ignore invalid progress updates");
  window.dispatch("slai:startup-error", { detail: "Unable to initialize" } as never);
  assert.ok(panel.classes.has("failed"));
  assert.equal(status.textContent, "Unable to initialize");
  window.dispatch("slai:startup-update", { detail: { progress: 1 } });
  assert.equal(status.textContent, "Unable to initialize", "an error must not be overwritten by loading text");
  assert.ok(panel.children.some(n => n.tag === "button"), "existing shared loader supplies a retry control");
  assert.equal(panel.attrs.get("removed"), undefined, "an initialization failure leaves the retry screen visible");
});

test("other SLAI apps keep the original shared startup behavior", () => {
  const body = new Node("body");
  const document = { body, currentScript: { dataset: { name: "SReader", logo: "logo.png" } },
    createElement: (tag: string) => new Node(tag) };
  const window = { addEventListener() {}, location: { href: "http://localhost/", origin: "http://localhost" } };
  vm.runInNewContext(readFileSync(loader, "utf8"), { document, window, Number,
    Image: class extends Node { constructor() { super("img"); } }, AbortController, setTimeout: () => 1, clearTimeout: () => {} });
  const panel = body.children[0];
  assert.equal(panel.className, "slai-startup");
  assert.equal(panel.children[1].textContent, "Loading SReader");
  assert.equal(panel.querySelector("progress"), null);
});

test("SPADRA startup CSS has an unframed responsive composition and reduced-motion fallback", () => {
  const css = readFileSync(join(app, "spadra.css"), "utf8");
  assert.match(css, /dialog\.slai-startup\.spadra-startup\[open\]/);
  assert.match(css, /--spadra-startup-size:\s*min\(40vw,/);
  assert.match(css, /@media \(orientation: portrait\)/);
  assert.match(css, /--spadra-startup-size:\s*min\(72vw,\s*22vh,/);
  assert.match(css, /border:\s*0;/);
  assert.match(css, /box-shadow:\s*none;/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});
