/** Native SLDC report preview and browser-print pagination.
 *
 * Uses the same physical-page DOM for the on-screen preview and the printable
 * PDF derivative. Browser layout is measured rather than estimated by
 * character counts; all content pagination is derived from the snapshot's
 * semantic blocks. The editable SLDC itself remains authoritative.
 */
import type { ReportView } from "./types.js";

type ReportBlock = {
  id: string; type: string; section?: string; text: string;
  attributes?: { level?: number; page_break_before?: boolean };
  table?: { columns: number; header_rows: number; rows: Array<Array<{ text: string; colspan?: number; rowspan?: number }>> };
};
type ReportDocument = { blocks: ReportBlock[]; metadata?: Record<string, string> };
interface Page { element: HTMLElement; body: HTMLElement; kind: "cover" | "front" | "body"; number: number }

const SIZES: Record<string, [number, number]> = {
  A5: [148, 210], A4: [210, 297], A3: [297, 420], B5: [176, 250],
  Letter: [215.9, 279.4], Legal: [215.9, 355.6], Tabloid: [279.4, 431.8],
};
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
};
const rowsOf = (b: ReportBlock): Array<Array<{ text: string; colspan?: number; rowspan?: number }>> =>
  b.table?.rows ?? [];

function makeTable(b: ReportBlock, rows: ReturnType<typeof rowsOf>, continuation = false): HTMLTableElement {
  const table = el("table", "report-table");
  table.setAttribute("aria-label", b.text || "Report data");
  if (b.text && !continuation) table.appendChild(el("caption", "", b.text));
  const count = b.table?.header_rows ?? 0;
  const head = el("thead"), body = el("tbody");
  rows.forEach((cells, index) => {
    const tr = el("tr");
    cells.forEach(cell => {
      const c = el(index < count ? "th" : "td", "", cell.text);
      if (cell.colspan && cell.colspan > 1) c.colSpan = cell.colspan;
      if (cell.rowspan && cell.rowspan > 1) c.rowSpan = cell.rowspan;
      tr.append(c);
    });
    (index < count ? head : body).appendChild(tr);
  });
  if (head.children.length) table.append(head);
  if (body.children.length) table.append(body);
  return table;
}

function makeBlock(b: ReportBlock, text?: string): HTMLElement {
  if (b.type === "table") return makeTable(b, rowsOf(b));
  const level = b.type === "heading" ? Math.max(1, Math.min(3, b.attributes?.level ?? 1)) : 0;
  const tag = level ? (`h${level}` as "h1" | "h2" | "h3") : b.type === "list_item" ? "p" : "p";
  const node = el(tag, level ? "report-heading" : b.type === "list_item" ? "report-list-item" : "report-paragraph", text ?? b.text);
  if (level) node.id = "report-heading-" + b.id;
  return node;
}

function makePage(kind: Page["kind"], width: number, height: number, reportName: string, number: number): Page {
  const element = el("section", `report-page report-page-${kind}`);
  element.style.width = `${width}mm`;
  element.style.height = `${height}mm`;
  element.setAttribute("aria-label", kind === "body" ? `Content page ${number}` : kind === "cover" ? "Report cover" : "Table of contents");
  const body = el("div", "report-page-body");
  element.append(body);
  if (kind === "body") {
    const footer = el("footer", "report-footer");
    footer.append(el("span", "", reportName), el("span", "report-page-number"));
    element.append(footer);
  }
  return { element, body, kind, number };
}

function fits(page: Page): boolean {
  // Precision margin absorbs fractional CSS layout rounding, not missing text.
  return page.body.scrollHeight <= page.body.clientHeight + 0.75;
}

function canPlace(page: Page, node: HTMLElement): boolean {
  page.body.append(node);
  if (fits(page)) return true;
  node.remove();
  return false;
}

/** Split an exceptionally long paragraph at actual browser line breaks, never clipped. */
function splitText(text: string, page: Page, newPage: () => Page, b: ReportBlock): Page {
  let remaining = text.trim();
  let current = page;
  let safety = 0;
  while (remaining) {
    if (++safety > 10000) throw new Error("Report paragraph exceeds pagination limit");
    const whole = makeBlock(b, remaining);
    if (canPlace(current, whole)) break;
    if (current.body.children.length > 0) {
      current = newPage();
      if (canPlace(current, whole)) break;
    }
    const tokens = remaining.match(/\S+\s*/gu) ?? [];
    if (!tokens.length) break;
    let lo = 1, hi = tokens.length, best = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const probe = makeBlock(b, tokens.slice(0, mid).join("").trimEnd());
      if (canPlace(current, probe)) { best = mid; lo = mid + 1; probe.remove(); }
      else hi = mid - 1;
    }
    if (best === 0) {
      // A single unbreakable token may exceed the width; split by characters.
      const chars = Array.from(remaining), probe = (count: number) => makeBlock(b, chars.slice(0, count).join(""));
      let left = 1, right = chars.length, count = 0;
      while (left <= right) {
        const mid = (left + right) >>> 1;
        const candidate = probe(mid);
        if (canPlace(current, candidate)) { count = mid; candidate.remove(); left = mid + 1; }
        else right = mid - 1;
      }
      if (!count) throw new Error("Report text cannot fit inside the selected paper margins");
      const part = chars.slice(0, count).join("");
      current.body.append(makeBlock(b, part));
      remaining = chars.slice(count).join("").trimStart();
    } else {
      const part = tokens.slice(0, best).join("").trimEnd();
      current.body.append(makeBlock(b, part));
      remaining = tokens.slice(best).join("").trimStart();
    }
    if (remaining) current = newPage();
  }
  return current;
}

/** Build pages by measuring real DOM heights and assigning ToC destinations. */
export function paginatedReport(view: ReportView): {
  preview: HTMLElement; print: () => void; pageCount: number; tocCount: number;
} {
  const source = view.document as ReportDocument | undefined;
  if (!source?.blocks) throw new Error("The report has no native block structure");
  const metadata = source.metadata ?? {};
  const paper = metadata["paper"] ?? "A4";
  const orientation = metadata["orientation"] ?? "portrait";
  const dimensions = SIZES[paper];
  if (!dimensions) throw new Error("Unsupported report paper: " + paper);
  const [width, height] = orientation === "landscape" ? [dimensions[1], dimensions[0]] : dimensions;
  const stage = el("div", "report-measuring");
  document.body.appendChild(stage);
  const title = view.report.name, pages: Page[] = [], contentPages: Page[] = [];
  const newPage = (kind: Page["kind"]): Page => {
    if (pages.length >= 10000) throw new Error("Report exceeds 10,000 pages");
    const page = makePage(kind, width, height, title, kind === "body" ? contentPages.length + 1 : 0);
    pages.push(page); stage.append(page.element);
    if (kind === "body") contentPages.push(page);
    return page;
  };
  try {
    const cover = newPage("cover");
    const heading = (tag: "h1" | "h2" | "h3", text: string) => el(tag, "report-cover-line", text);
    const coverContent = el("div", "report-cover-content");
    const image = el("img", "report-logo");
    image.src = "/assets/img/spadra_main.png";
    image.alt = "SPADRA official logo";
    coverContent.append(
      image, heading("h1", "SPADRA"),
      el("p", "report-tagline", "Intelligent CAD, BIM & Engineering powered by SLAI"),
      heading("h2", title), el("p", "", `Made by ${metadata["author"] || "—"}`),
      el("p", "", metadata["created"] ?? view.report.created),
    );
    cover.body.append(coverContent);

    let current = newPage("body");
    const headingDestinations: Array<{ id: string; name: string; level: number; page: number }> = [];
    for (const block of source.blocks.filter(b => b.section === "body" || (!b.section && b.type !== "figure"))) {
      if (block.type === "heading") {
        // Every semantic heading starts its own content page.
        if (current.body.children.length) current = newPage("body");
        const node = makeBlock(block);
        if (!canPlace(current, node)) throw new Error("Report heading does not fit on the page");
        headingDestinations.push({ id: node.id, name: block.text, level: block.attributes?.level ?? 1, page: current.number });
        continue;
      }
      if (block.type === "table") {
        const tableRows = rowsOf(block), n = block.table?.header_rows ?? 0;
        let currentRows = tableRows.slice(0, n), table = makeTable(block, currentRows);
        let inserted = false;
        for (const row of tableRows.slice(n)) {
          const next = makeTable(block, [...currentRows, row], inserted);
          if (!canPlace(current, next)) {
            if (currentRows.length > n) {
              if (table.isConnected) table.remove();
              current.body.append(table);
              current = newPage("body");
            } else if (current.body.children.length) current = newPage("body");
            else throw new Error("A report table row exceeds the printable page area");
            currentRows = tableRows.slice(0, n);
            table = makeTable(block, [...currentRows, row], true);
            if (!canPlace(current, table)) throw new Error("A report table row is too tall for one page");
            currentRows.push(row); inserted = true;
          } else {
            if (table.isConnected) table.remove();
            table = next; currentRows.push(row);
            inserted = true;
          }
        }
        if (!inserted && !canPlace(current, table)) {
          current = newPage("body");
          if (!canPlace(current, table)) throw new Error("Report table header cannot fit on one page");
        }
        continue;
      }
      current = splitText(block.text, current, () => newPage("body"), block);
    }
    if (!current.body.children.length && contentPages.length > 1) {
      current.element.remove(); pages.splice(pages.indexOf(current), 1); contentPages.pop();
    }

    // ToC starts on physical page two, but its entries use content-only
    // numbering. No iterative guessing is needed: content numbers are
    // independent from the number of ToC pages.
    const tocPages: Page[] = [];
    let toc = newPage("front");
    tocPages.push(toc);
    toc.body.append(el("h1", "report-toc-title", "Table of Contents"));
    for (const h of headingDestinations.filter(x => x.level <= 3)) {
      const row = el("a", `report-toc-entry report-toc-level-${h.level}`);
      row.href = "#" + h.id;
      row.append(el("span", "", h.name), el("span", "report-toc-dots"),
                 el("span", "report-toc-number", String(h.page)));
      if (!canPlace(toc, row)) {
        toc = newPage("front"); tocPages.push(toc);
        toc.body.append(el("h1", "report-toc-title", "Table of Contents (continued)"));
        if (!canPlace(toc, row)) throw new Error("The table of contents cannot fit on the selected page");
      }
    }
    // Preserve cover, then ToC, then content in physical output order.
    const ordered = [cover, ...tocPages, ...contentPages];
    ordered.forEach(p => {
      if (p.kind !== "body") return;
      const label = p.element.querySelector(".report-page-number");
      if (label) label.textContent = `Page ${p.number} of ${contentPages.length}`;
    });
    const preview = el("div", "report-preview");
    ordered.forEach(p => preview.append(p.element));
    const print = (): void => {
      const printRoot = preview.cloneNode(true) as HTMLElement;
      printRoot.classList.add("report-print-root");
      const style = document.createElement("style");
      style.textContent = `@page { size: ${width}mm ${height}mm; margin: 0; }`;
      document.head.append(style);
      document.body.append(printRoot);
      const cleanup = (): void => { printRoot.remove(); style.remove(); };
      window.addEventListener("afterprint", cleanup, { once: true });
      window.print();
    };
    return { preview, print, pageCount: contentPages.length, tocCount: tocPages.length };
  } finally {
    stage.remove();
  }
}
