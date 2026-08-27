import type { CaseStatus } from "../types";

// React escapes text content automatically (unlike the old innerHTML-based
// vanilla JS version, which needed a manual esc() helper everywhere) --
// nothing here needs to sanitize HTML, only format values.

export function rupees(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

export function displayRootCause(rootCause: string): string {
  return rootCause.startsWith(MOCK_ROOT_CAUSE_PREFIX)
    ? rootCause.slice(MOCK_ROOT_CAUSE_PREFIX.length)
    : rootCause;
}
