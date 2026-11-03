/** Canvas and WebGL cannot read `light-dark()` or `var()` themselves, so a hidden probe element lets the browser resolve a theme colour to rgb(). */
let probe = null;
export function cssColor(name, fallback) {
    if (!probe || !probe.isConnected) {
        probe = document.createElement("span");
        probe.setAttribute("aria-hidden", "true");
        for (const [k, v] of Object.entries({ position: "absolute", visibility: "hidden", "pointer-events": "none", width: "0", height: "0" }))
            probe.style.setProperty(k, v);
        document.body.appendChild(probe);
    }
    probe.style.setProperty("color", fallback);
    probe.style.setProperty("color", `var(${name}, ${fallback})`);
    return getComputedStyle(probe).color || fallback;
}
/** "rgb(26, 95, 209)" or "rgba(…)" as 0..1 channels. Anything unparseable becomes opaque dark ink rather than throwing. */
export function rgba(color) {
    const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/.exec(color);
    if (!m)
        return [0.1, 0.1, 0.12, 1];
    const alpha = m[4] === undefined ? 1 : m[4].endsWith("%") ? Number.parseFloat(m[4]) / 100 : Number(m[4]);
    return [Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255, alpha];
}
export function withAlpha(color, alpha) {
    const [r, g, b] = rgba(color);
    return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${alpha})`;
}
