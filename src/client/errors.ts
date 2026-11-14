/** How a failure is presented: what kind of problem it is, what happened, and what to do next. The kinds and the advice come from the server (start-up data),
 *  so the screen never holds a table of its own; with none, the generic advice applies. */
import type { ErrorInfo } from "./types.js";

export interface Presented { code: string; category: string; message: string; recovery: string }

const GENERIC: ErrorInfo = { category: "Unexpected", recovery: "Nothing was changed. Try again." };

/** The category and advice for an error code: an exact entry, else a storage problem for any ``storage_*`` code, else the generic one. */
export function classify(code: string, table: Record<string, ErrorInfo> | undefined): ErrorInfo {
  const t = table ?? {};
  return t[code] ?? (code.startsWith("storage_") ? t["storage_error"] : undefined) ?? t["error"] ?? GENERIC;
}

/** The error as shown: its own message, and its own hint where it has one (it knows more than the category does). */
export function present(error: { code: string; message: string; hint?: string | undefined }, table: Record<string, ErrorInfo> | undefined): Presented {
  const info = classify(error.code, table);
  return { code: error.code, category: info.category, message: error.message, recovery: error.hint || info.recovery };
}

/** One line for a toast: "Input: message Advice". */
export function oneLine(p: Presented): string {
  const message = /[.!?]$/.test(p.message) ? p.message : `${p.message}.`;
  return `${p.category}: ${message} ${p.recovery}`.trim();
}

/** A failure that is not from the server (a lost connection, a bug in the page). The cause is shown; nothing else is guessed. */
export function presentUnexpected(error: unknown, what: string): Presented {
  const cause = error instanceof Error && error.message ? error.message : String(error);
  return { code: "client", category: "Unexpected", message: `${what} failed unexpectedly (${cause}).`, recovery: "Nothing was changed. Try again; if it keeps happening, see the diagnostics log." };
}

export interface Fix { label: string; steps: Array<{ op: string; params: Record<string, unknown> }> }
export interface FixDetails { element?: string; part?: string; cell?: string; positions_mm?: number[]; gap_mm?: number; needed_mm?: number; fixes?: Fix[] }

/** The corrections a server error offers (``details.fixes``), each a label and the commands that make it; none when the error has no such details. */
export function fixesOf(error: { details: unknown }): Fix[] {
  const d = error.details as FixDetails | null | undefined;
  return Array.isArray(d?.fixes) ? d.fixes.filter((f) => f && typeof f.label === "string" && Array.isArray(f.steps) && f.steps.length > 0) : [];
}
