/** DOM helpers shared by every SLAI application. Elements are built with createElement and textContent only: no innerHTML anywhere. */
const SVG_NS = "http://www.w3.org/2000/svg";
const PROPERTY_FIRST = new Set(["value", "checked", "disabled", "hidden", "selected", "open", "indeterminate", "readOnly", "required", "min", "max", "step", "type", "placeholder"]);
function append(parent, child) {
    if (child === null || child === undefined || child === false)
        return;
    if (Array.isArray(child)) {
        for (const item of child)
            append(parent, item);
        return;
    }
    parent.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
}
function apply(el, props) {
    if (!props)
        return;
    for (const [name, value] of Object.entries(props)) {
        if (value === undefined || value === null)
            continue;
        if (name === "class")
            el.setAttribute("class", String(value));
        else if (name === "text")
            el.textContent = String(value);
        else if (name === "css")
            for (const [prop, v] of Object.entries(value))
                el.style.setProperty(prop, v);
        else if (name === "data")
            for (const [key, v] of Object.entries(value)) {
                if (v !== undefined)
                    el.dataset[key] = v;
            }
        else if (name === "attrs")
            for (const [attr, v] of Object.entries(value)) {
                if (v === undefined || v === null || v === false)
                    continue;
                el.setAttribute(attr, v === true ? "" : String(v));
            }
        else if (name === "on")
            for (const [type, handler] of Object.entries(value))
                el.addEventListener(type, handler);
        else if (PROPERTY_FIRST.has(name) || name in el)
            el[name] = value;
        else
            el.setAttribute(name, String(value));
    }
}
/** Build an HTML element. `h("button", {class: "btn", on: {click}}, "Save")`. */
export function h(tag, props, ...children) {
    const el = document.createElement(tag);
    apply(el, props);
    for (const child of children)
        append(el, child);
    return el;
}
/** Build an SVG element (namespace-aware). Attributes go in `attrs`. */
export function s(tag, props, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    apply(el, props);
    for (const child of children)
        append(el, child);
    return el;
}
export function clear(node) { while (node.firstChild)
    node.removeChild(node.firstChild); }
export function replace(node, ...children) {
    clear(node);
    for (const child of children)
        append(node, child);
}
/** querySelector that throws when the element is missing, so a typo fails loudly instead of at the first use. */
export function $(selector, root = document) {
    const found = root.querySelector(selector);
    if (!found)
        throw new Error(`Missing element: ${selector}`);
    return found;
}
export function $$(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
}
let counter = 0;
/** A document-unique id for label/aria wiring. */
export function nextId(prefix = "id") { counter += 1; return `${prefix}-${counter}`; }
/** Wrap a control in the shared `.field` layout: label above, optional hint and error below, aria wired up. */
export function labelled(label, control, options = {}) {
    const id = control.id || nextId("f");
    control.id = id;
    const wrap = h("div", { class: `field${options.className ? ` ${options.className}` : ""}` }, h("label", { attrs: { for: id }, text: label }), control);
    if (options.hint) {
        const hint = h("span", { class: "hint", text: options.hint, attrs: { id: `${id}-hint` } });
        control.setAttribute("aria-describedby", hint.id);
        wrap.appendChild(hint);
    }
    if (options.error) {
        const error = h("span", { class: "error", text: options.error, attrs: { id: `${id}-error`, role: "alert" } });
        control.setAttribute("aria-invalid", "true");
        control.setAttribute("aria-describedby", error.id);
        wrap.appendChild(error);
    }
    return wrap;
}
let live = null;
/** Say something to screen readers without moving focus. */
export function announce(message, politeness = "polite") {
    if (!live || !live.isConnected) {
        live = h("div", { class: "visually-hidden", attrs: { "aria-live": politeness, "aria-atomic": "true", role: politeness === "assertive" ? "alert" : "status" } });
        document.body.appendChild(live);
    }
    live.setAttribute("aria-live", politeness);
    live.textContent = "";
    window.setTimeout(() => { if (live)
        live.textContent = message; }, 30);
}
/** Focusable descendants in tab order, for focus traps and roving menus. */
export function focusable(root) {
    return $$('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', root)
        .filter((el) => !el.hidden && el.getClientRects().length > 0);
}
