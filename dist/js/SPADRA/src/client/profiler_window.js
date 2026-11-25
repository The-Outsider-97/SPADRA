/** The Profiler window's geometry: where the pop-up sits and how large it is. Kept free of the DOM so it can be tested on its own (tests_ts/profiler_window.test.ts). */
/** A normal window takes three quarters of the screen in each direction. */
export const WINDOW_FRACTION = 0.75;
/** Smaller than this the ribbon is cut off (the style sheet uses the same numbers), unless the screen itself is smaller. */
export const MIN_WINDOW = { w: 704, h: 480 };
/** The first size of the pop-up: three quarters of the screen each way, centred. */
export function defaultGeometry(vp) {
    const w = Math.round(vp.w * WINDOW_FRACTION), h = Math.round(vp.h * WINDOW_FRACTION);
    return { x: Math.round((vp.w - w) / 2), y: Math.round((vp.h - h) / 2), w, h };
}
/** Keep a window on the screen: never larger than the screen, never smaller than MIN_WINDOW (when the screen allows), and never moved out of reach. */
export function clampGeometry(g, vp) {
    const w = Math.round(Math.min(vp.w, Math.max(Math.min(MIN_WINDOW.w, vp.w), g.w)));
    const h = Math.round(Math.min(vp.h, Math.max(Math.min(MIN_WINDOW.h, vp.h), g.h)));
    return { w, h, x: Math.round(Math.min(Math.max(0, g.x), vp.w - w)), y: Math.round(Math.min(Math.max(0, g.y), vp.h - h)) };
}
/** The state a double-click on the title bar leads to: a normal window grows to full screen, anything else goes back to the pop-up. */
export function toggledState(state) { return state === "normal" ? "max" : "normal"; }
