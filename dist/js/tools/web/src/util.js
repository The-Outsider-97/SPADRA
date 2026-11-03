/** Small dependency-free helpers shared by every SLAI application. */
export function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }
export function debounce(fn, waitMs) {
    let timer;
    let pending;
    const run = () => { timer = undefined; const args = pending; pending = undefined; if (args)
        fn(...args); };
    const wrapped = ((...args) => { pending = args; if (timer !== undefined)
        window.clearTimeout(timer); timer = window.setTimeout(run, waitMs); });
    wrapped.flush = () => { if (timer !== undefined) {
        window.clearTimeout(timer);
        run();
    } };
    wrapped.cancel = () => { if (timer !== undefined)
        window.clearTimeout(timer); timer = undefined; pending = undefined; };
    return wrapped;
}
/** Random 32-hex id (same shape as the server's `new_id`), for optimistic client-side records. */
export function uid() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
/** A tiny typed event emitter, so views can subscribe to store changes without a framework. */
export class Emitter {
    listeners = new Set();
    on(listener) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
    emit(value) { for (const listener of [...this.listeners])
        listener(value); }
    get size() { return this.listeners.size; }
}
/** Natural, case-insensitive text comparison for lists. */
export function compareText(a, b) { return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }); }
/** "1.4 MB" style sizes. */
export function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0)
        return "—";
    if (bytes < 1024)
        return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
/** "1 note", "3 notes", "0 notes". Pass the plural when it is not just an added "s" (plural(2, "box", "boxes")). */
export function plural(count, one, many = `${one}s`) {
    return `${count} ${count === 1 ? one : many}`;
}
