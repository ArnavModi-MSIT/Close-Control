import type { CaseStatus } from "../types";

// React escapes text content automatically (unlike the old innerHTML-based
// vanilla JS version, which needed a manual esc() helper everywhere) --
// nothing here needs to sanitize HTML, only format values.

export function rupees(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// A full-precision rupee string ("₹1,05,18,329.39") is 15-16 characters --
// verified directly (not assumed) that this genuinely overflows a KpiCards
// tile at the six-column desktop layout's narrowest realistic width
// (scrollWidth 151px vs. a 116px content box at 1024px viewport). Shrinking
// the font to force-fit was the wrong axis to fight on; the actual fix is
// that a KPI tile isn't where full paisa precision belongs. Real Indian
// financial dashboards abbreviate large rupee figures to Lakh/Crore for
// exactly this reason -- the exact value stays one hover away via the
// element's title, nothing is lost, only what's ALWAYS visible shrinks.
export function rupeesCompact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1_00_00_000) return `${sign}₹${(abs / 1_00_00_000).toFixed(2)}Cr`;
  if (abs >= 1_00_000) return `${sign}₹${(abs / 1_00_000).toFixed(2)}L`;
  return rupees(n);
}

export const STATUS_LABELS: Record<CaseStatus, string> = {
  auto_resolved: "AI auto-resolved",
  pending: "Pending",
  pending_manager_approval: "Awaiting manager",
  approved: "Approved",
  overridden: "Overridden",
  escalated: "Escalated",
  auto_closed: "Auto-closed (re-verified)",
};

export function statusLabel(s: string): string {
  return STATUS_LABELS[s as CaseStatus] ?? s;
}

export const STATUS_COLORS: Record<CaseStatus, string> = {
  auto_resolved: "#8B2560", // --color-accent
  pending: "#A68A8E",       // --color-ink-mute
  pending_manager_approval: "#A8720A", // --color-warn
  approved: "#0B8F2F",      // --color-good
  overridden: "#8B2560",    // --color-accent
  escalated: "#C23A34",     // --color-crit
  auto_closed: "#0B8F2F",   // --color-good -- genuinely resolved, just not by a human
};

const ACTION_LABELS: Record<string, string> = {
  proposed: "proposed",
  investigated: "investigated",
  approved: "approved",
  overridden: "overrode",
  escalated: "escalated",
  reverted: "reverted",
  auto_closed: "auto-closed",
};

export function actionLabel(a: string): string {
  return ACTION_LABELS[a] ?? a;
}

export function formatTimestamp(ts: string | null): string {
  if (!ts) return "time not recorded";
  return new Date(ts).toLocaleString();
}

// agent/providers/mock.py writes this exact prefix into root_cause so the
// raw audit log is self-explanatory read on its own, with no UI in between.
// The frozen data is never changed (that would violate the "AI's original
// proposal is immutable" rule) -- this only affects what's DISPLAYED, moving
// the same fact into a proper badge (see DetailPanel's "AI proposal"
// section) instead of an inline bracket in the middle of a sentence.
const MOCK_ROOT_CAUSE_PREFIX = "[MOCK PROVIDER -- not a real LLM response] ";

// Raw exception_type/matcher labels are snake_case ("missing_bank_reference")
// -- readable enough in a wide code block, but underscores aren't word-break
// points in CSS, so as a single unbroken token it forces its containing
// table column to grow rather than wrapping, which is what was pushing
// CaseTable wider than its panel and forcing horizontal scroll. Spaces
// give the browser real wrap points and read better regardless.
export function humanizeType(raw: string): string {
  return raw.replace(/_/g, " ");
}

export function displayRootCause(rootCause: string): string {
  return rootCause.startsWith(MOCK_ROOT_CAUSE_PREFIX)
    ? rootCause.slice(MOCK_ROOT_CAUSE_PREFIX.length)
    : rootCause;
}

// Same convention as MOCK_ROOT_CAUSE_PREFIX above, generalized: any
// "[MOCK PROVIDER ...] " bracket at the start of a string is this
// project's own transparency marker, not content -- agent/run_summary.py
// uses a differently-worded one ("...deterministic template...") than
// agent/providers/mock.py's per-case root_cause does, so this strips
// whichever bracket is actually present rather than coupling to one
// exact string on both sides of the Python/TypeScript boundary.
const MOCK_BRACKET_RE = /^\[MOCK PROVIDER[^\]]*\]\s*/;

export function isMockText(text: string): boolean {
  return MOCK_BRACKET_RE.test(text);
}

export function stripMockBracket(text: string): string {
  return text.replace(MOCK_BRACKET_RE, "");
}
