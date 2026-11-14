/** Canvas and WebGL cannot read `light-dark()` or `var()` themselves, so a hidden probe element lets the browser resolve a theme colour to rgb(). */
let probe: HTMLElement | null = null;

export function cssColor(name: string, fallback: string): string {
  if (!probe || !probe.isConnected) {
    probe = document.createElement("span");
    probe.setAttribute("aria-hidden", "true");
    for (const [k, v] of Object.entries({ position: "absolute", visibility: "hidden", "pointer-events": "none", width: "0", height: "0" })) probe.style.setProperty(k, v);
    document.body.appendChild(probe);
  }
  probe.style.setProperty("color", fallback);
  probe.style.setProperty("color", `var(${name}, ${fallback})`);
  return getComputedStyle(probe).color || fallback;
}

/** "rgb(26, 95, 209)" or "rgba(…)" as 0..1 channels. Anything unparseable becomes opaque dark ink rather than throwing. */
export function rgba(color: string): [number, number, number, number] {
  const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/.exec(color);
  if (!m) return [0.1, 0.1, 0.12, 1];
  const alpha = m[4] === undefined ? 1 : m[4].endsWith("%") ? Number.parseFloat(m[4]) / 100 : Number(m[4]);
  return [Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255, alpha];
}

/** Six-digit material HEX colors as normalized WebGL channels. */
export function hexRgb01(hex: string): [number, number, number] {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) throw new Error("Expected a #RRGGBB color");
  return [1, 3, 5].map(i => Number.parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

export function withAlpha(color: string, alpha: number): string {
  const [r, g, b] = rgba(color);
  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${alpha})`;
}
