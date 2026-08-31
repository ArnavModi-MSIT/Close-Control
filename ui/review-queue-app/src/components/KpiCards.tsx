import type { StatsResponse } from "../types";
import { rupees, rupeesCompact, statusLabel } from "../lib/format";

function Kpi({ value, label, accent, title }: { value: string; label: string; accent?: boolean; title?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-surface p-4" title={title}>
      {/* A full-precision rupee string ("₹1,05,18,329.39") is 15-16
          characters -- verified directly (not assumed) that this overflows
          a tile at the six-column desktop layout's narrowest realistic
          width. Two things fixed it, not just font-shrinking: money values
          are now passed in already Lakh/Crore-abbreviated (rupeesCompact(),
          6-8 chars, see lib/format.ts), so a normal readable size fits with
          real room to spare -- whitespace-nowrap stays only as a guarantee
          it never silently wraps mid-word again, not as the thing doing
          the work. The exact paisa-precision figure is one hover away via
          this card's own title, never lost. */}
      <div className={`whitespace-nowrap font-mono text-[1.2rem] font-bold leading-tight ${accent ? "text-accent" : "text-ink"}`}>
        {value}
      </div>
      <div className="mt-0.5 text-[0.72rem] tracking-wide text-ink-soft uppercase">
        {label}
        {title && <span className="ml-1 text-ink-mute">*</span>}
      </div>
    </div>
  );
}

// Appends the exact rupee figure to whatever tooltip a card already carries
// (e.g. KpiCards' own stream-mode caveat), rather than one silently
// replacing the other.
function withExactValue(existing: string | undefined, exactRupees: string): string {
  return existing ? `${existing} Exact: ${exactRupees}.` : `Exact: ${exactRupees}`;
}

export function KpiCards({ stats, streamMode }: { stats: StatsResponse; streamMode: boolean }) {
  const totalAtRisk = Object.values(stats.amount_at_risk_rupees_by_status).reduce((a, b) => a + b, 0);
  const needsHumanNow = stats.counts_by_status.pending + stats.counts_by_status.pending_manager_approval;
  const cp = stats.cash_position;
  const ct = stats.cycle_time;
  // Cash-position/reconciliation figures are computed "as of" a fixed
  // reference date (cash_position/config.py's DEFAULT_AS_OF), not the
  // stream's own advancing simulated clock -- CLAUDE.md documents this as
  // a known limitation, and an external review flagged that showing both
  // side by side in stream mode could read as internally inconsistent
  // (why does the "live" badge move but this number doesn't?). Rather than
  // rearchitect the cash-position endpoints to track a live clock (a
  // materially bigger change for a local demo), label the distinction
  // explicitly wherever it could actually be seen next to a "live" badge.
  const cashPositionTitle = streamMode
    ? "Cash position: static reference date (not the stream's live simulated clock) -- see CLAUDE.md §7's known limitations"
    : undefined;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Kpi value={String(stats.total_cases)} label="Total cases" />
      <Kpi value={String(stats.counts_by_status.auto_resolved)} label="AI auto-resolved" accent />
      <Kpi value={String(stats.counts_by_status.auto_closed ?? 0)} label="Auto-closed (re-verified)" accent />
      <Kpi value={String(needsHumanNow)} label="Needs a human" />
      <Kpi value={String(stats.investigated_count)} label="Investigated" />
      <Kpi value={String(stats.counts_by_tier["2"])} label="Tier 2 (≥₹50k)" />
      <Kpi value={rupeesCompact(totalAtRisk)} label="Total at risk" title={withExactValue(undefined, rupees(totalAtRisk))} />
      {/* Money-centric figures -- real cash_position/engine.py output, see
          review_backend/main.py's _cash_position_stats(). Null (not zero)
          when the underlying data isn't scoreable yet -- rupees() already
          renders that as "—", never a misleading 0. */}
      {/* RBI TAT breach count -- see review_backend/sla.py. Rendered as a
          critical-status figure rather than the accent colour: this is an
          overdue-obligation signal, not an AI/branding one. */}
      <Kpi
        value={String(stats.sla?.breached_count ?? 0)}
        label={`Past RBI T+${stats.sla?.tat_business_days ?? 5}`}
        title={
          stats.sla
            ? `${stats.sla.breached_count} of ${stats.sla.open_cases_checked} still-open cases are past RBI's T+${stats.sla.tat_business_days} business-day resolution deadline (as of ${stats.sla.as_of}). ` +
              `Rs.${stats.sla.compensation_exposure_rupees.toLocaleString("en-IN")} of Rs.100/day compensation has accrued across ${stats.sla.total_days_overdue} overdue days. ` +
              `Worst: ${stats.sla.worst_case_transaction_id ?? "—"} at ${stats.sla.worst_case_days_overdue} days.`
            : undefined
        }
      />
      {/* Complementary to the SLA card above, not a duplicate of it: SLA is
          a REGULATORY deadline (RBI's fixed T+5), this is an OPERATIONAL
          one -- how long a case actually sits at its current stage, which
          matters even for a case comfortably inside its SLA window. See
          review_backend/cycle_time.py's own docstring for why this is
          measured against real wall-clock time while sla.py deliberately
          isn't. */}
      <Kpi
        value={ct.bottleneck_status ? `${ct.bottleneck_avg_days?.toFixed(1)}d` : "—"}
        label={ct.bottleneck_status ? `Stuck in ${statusLabel(ct.bottleneck_status).toLowerCase()}` : "Bottleneck"}
        title={
          ct.bottleneck_status
            ? Object.entries(ct.by_status)
                .filter(([s]) => s === "pending" || s === "pending_manager_approval")
                .map(([s, e]) =>
                  `${statusLabel(s)}: ${e.currently_open_count} case(s) waiting, avg ${e.currently_open_avg_days?.toFixed(1) ?? "—"}d so far` +
                  (e.oldest_open_transaction_id ? ` (oldest: ${e.oldest_open_transaction_id}, ${e.oldest_open_days?.toFixed(1)}d)` : "") +
                  (e.completed ? `. Historically takes ${e.completed.median_days.toFixed(1)}d median to clear this stage (${e.completed.count} completed).` : ". No cases have cleared this stage yet to measure a historical duration.")
                )
                .join(" | ")
            : "No cases are currently waiting on a human."
        }
      />
      <Kpi value={cp ? rupeesCompact(cp.confirmed_rupees) : "—"} label="Reconciled ₹" accent
           title={cp ? withExactValue(cashPositionTitle, rupees(cp.confirmed_rupees)) : cashPositionTitle} />
      <Kpi value={cp ? rupeesCompact(cp.in_transit_rupees) : "—"} label="In transit ₹"
           title={cp ? withExactValue(cashPositionTitle, rupees(cp.in_transit_rupees)) : cashPositionTitle} />
      {/* The 70.2% below has NO visible anchor without this card -- "617
          Total cases" (a few cards to the left) is a completely different,
          smaller number (the escalated review queue), not what the rate is
          computed over. Placed immediately before Automation rate so the
          denominator sits right next to the percentage that depends on it,
          rather than left implicit in a tooltip a reader has to find. */}
      <Kpi value={cp ? cp.total_ledger_transactions.toLocaleString("en-IN") : "—"} label="Transactions processed"
           title={cp ? `${cp.automation_numerator.toLocaleString("en-IN")} of ${cp.total_ledger_transactions.toLocaleString("en-IN")} resolved with zero ML/LLM involvement -- the actual basis for the Automation rate figure.` : undefined} />
      <Kpi value={cp ? `${cp.automation_rate_pct.toFixed(1)}%` : "—"} label="Automation rate" accent
           title={cp ? `${cp.automation_numerator.toLocaleString("en-IN")} of ${cp.total_ledger_transactions.toLocaleString("en-IN")} total ledger transactions -- not a percentage of the 617-case escalated queue.` : undefined} />
    </div>
  );
}
