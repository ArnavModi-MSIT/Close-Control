import type { StatsResponse } from "../types";
import { rupees } from "../lib/format";

function Kpi({ value, label, accent, title }: { value: string; label: string; accent?: boolean; title?: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4" title={title}>
      <div className={`font-mono text-[1.35rem] font-bold ${accent ? "text-accent" : "text-ink"}`}>{value}</div>
      <div className="mt-0.5 text-[0.72rem] tracking-wide text-ink-soft uppercase">
        {label}
        {title && <span className="ml-1 text-ink-mute">*</span>}
      </div>
    </div>
  );
}

export function KpiCards({ stats, streamMode }: { stats: StatsResponse; streamMode: boolean }) {
  const totalAtRisk = Object.values(stats.amount_at_risk_rupees_by_status).reduce((a, b) => a + b, 0);
  const needsHumanNow = stats.counts_by_status.pending + stats.counts_by_status.pending_manager_approval;
  const cp = stats.cash_position;
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
      <Kpi value={rupees(totalAtRisk)} label="Total at risk" />
      {/* Money-centric figures -- real cash_position/engine.py output, see
          review_backend/main.py's _cash_position_stats(). Null (not zero)
          when the underlying data isn't scoreable yet -- rupees() already
          renders that as "—", never a misleading 0. */}
      <Kpi value={cp ? rupees(cp.confirmed_rupees) : "—"} label="Reconciled ₹" accent title={cashPositionTitle} />
      <Kpi value={cp ? rupees(cp.in_transit_rupees) : "—"} label="In transit ₹" title={cashPositionTitle} />
      <Kpi value={cp ? `${cp.automation_rate_pct.toFixed(1)}%` : "—"} label="Automation rate" accent />
    </div>
  );
}
