import { $ } from "../../../tools/web/src/dom.js";
import { applyAppearance } from "../../../tools/web/src/theme.js";
import { toast } from "../../../tools/web/src/ui.js";
import { loadAppearance, newProjectDialog, openProjectDialog } from "./dialogs.js";
import { registerSpadraGlyphs } from "./icons.js";
import { Shell } from "./shell.js";
import { store } from "./store.js";
async function start() {
    applyAppearance(loadAppearance(), "soft");
    registerSpadraGlyphs();
    const root = $("#root");
    try {
        await store.init();
    }
    catch (error) {
        toast(error instanceof Error ? error.message : "SPADRA could not start.", { kind: "error", timeoutMs: 0 });
    }
    const shell = new Shell();
    root.replaceChildren(shell.el);
    if (!store.project)
        void (async () => { await openOrNew(); })();
}
/** With no project open, offer the two ways to begin. */
async function openOrNew() {
    if ((store.boot?.recent.length ?? 0) > 0)
        await openProjectDialog(store);
    else
        await newProjectDialog(store);
}
void start();
