/** Menus opened from SPADRA's own controls. The shared menu closes itself when anything scrolls; pressing a button that is partly out of view (in the ribbon or the tab strip)
 *  makes the browser scroll it into view *after* the click, which would close the menu the click just opened. The menu is therefore opened on the next frame, after that scroll. */
import { openMenu as openSharedMenu } from "../../../tools/web/src/ui.js";
export function openMenu(anchor, entries, options = {}) {
    requestAnimationFrame(() => openSharedMenu(anchor, entries, options));
}
