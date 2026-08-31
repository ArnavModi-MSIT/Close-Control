import { useState } from "react";
import { useMatcherAutoResolved } from "../hooks/useQueries";
import { rupees, humanizeType } from "../lib/format";
import type { MatcherAutoResolvedItem } from "../types";

// The 58 matcher-level auto-resolves are, by design, invisible everywhere
// else in this UI -- they never enter the review queue at all, since only
// cases the deterministic matcher could NOT resolve escalate there (see
// GET /api/matcher-auto-resolved's own docstring). That's the correct
// scope for the review queue, but it also meant a viewer had no way to
// see the matcher's own 70.2% zero-LLM resolution rate as real,
// individual transactions -- including any of the 18 real Razorpay
// Capital loan recoveries, the 4th data source. This panel exists purely
// to make that population visible; it's read-only, nothing here is
// reviewable or actionable, matching what these transactions actually
// are -- already, correctly, closed.
//
// Header/collapse chrome removed -- see ToolsHub.tsx, which now owns
// which one of the six tool panels is shown.

const TYPE_TABS = ["all", "loan_recovery_deduction", "timing_lag_beyond_t2", "fee_variance"] as const;

function ItemRow({ item }: { item: MatcherAutoResolvedItem }) {
  const isLoan = item.final_exception_type === "loan_recovery_deduction";
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[0.85rem] font-semibold text-ink">
          <span className="font-mono">{item.transaction_id}</span>
          {isLoan && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[0.68rem] font-bold text-accent"
                  title="Razorpay Capital's own recovery ledger -- the 4th data source, reconciled here.">
              CAPITAL RECOVERY
            </span>
          )}
        </div>
        <div className="truncate text-[0.76rem] text-ink-mute">
          {humanizeType(item.final_exception_type)} &middot; {item.merchant_id}
          {isLoan && item.loan_id && <> &middot; loan <span className="font-mono">{item.loan_id}</span></>}
        </div>
      </div>
      <div className="shrink-0 text-right">
        {isLoan ? (
          <>
            <div className="font-mono text-[0.85rem] font-semibold text-ink">
              {rupees(item.loan_recovery_amount_rupees)}
            </div>
            <div className="text-[0.72rem] text-ink-mute">recovered &mdash; reconciles the delta exactly</div>
          </>
        ) : (
          <>
            <div className="font-mono text-[0.85rem] font-semibold text-ink">
              {rupees(item.observed_net_rupees)}
            </div>
            <div className="text-[0.72rem] text-ink-mute">
              vs. {rupees(item.ledger_expected_net_rupees)} expected
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function MatcherAutoResolved() {
  const [tab, setTab] = useState<(typeof TYPE_TABS)[number]>("all");
  const { data, isLoading, isError, refetch } = useMatcherAutoResolved(
    true, tab === "all" ? undefined : tab,
  );

  return (
    <div className="px-6 py-5">
      <div className="mb-4">
        <h2 className="text-[1.05rem] font-bold text-ink">Matcher-Auto-Resolved</h2>
        <p className="mt-0.5 text-[0.82rem] text-ink-soft">
          The transactions the deterministic matcher closed on its own, before any LLM was
          ever involved &mdash; including every real Razorpay Capital loan recovery.
        </p>
      </div>

      {isLoading && <p className="py-6 text-center text-[0.9rem] text-ink-mute">Loading&hellip;</p>}

      {isError && (
        <div className="py-4">
          <p className="mb-3 text-[0.88rem] text-crit">Couldn't load matcher-resolved transactions.</p>
          <button type="button" onClick={() => refetch()}
                  className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit">
            Retry
          </button>
        </div>
      )}

      {data && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Total, zero LLM" value={String(data.total_matcher_auto_resolved)} accent />
            <Stat label="Loan recoveries" value={String(data.by_exception_type.loan_recovery_deduction ?? 0)} />
            <Stat label="Timing lag" value={String(data.by_exception_type.timing_lag_beyond_t2 ?? 0)} />
            <Stat label="Fee variance" value={String(data.by_exception_type.fee_variance ?? 0)} />
          </div>

          <div className="mb-3 flex flex-wrap gap-2">
            {TYPE_TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`rounded-full border-[1.5px] px-3 py-1 text-[0.76rem] font-semibold ${
                  tab === t ? "border-accent bg-accent-soft text-accent" : "border-border-2 bg-surface text-ink-soft"
                }`}
              >
                {t === "all" ? "All" : humanizeType(t)}
              </button>
            ))}
          </div>

          <div className="flex flex-col divide-y divide-border">
            {data.items.map((item) => <ItemRow key={item.transaction_id} item={item} />)}
            {data.items.length === 0 && (
              <p className="py-4 text-center text-[0.85rem] text-ink-mute">No transactions in this category.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-border bg-ground-2 px-3 py-2.5">
      <div className={`font-mono text-[1.15rem] font-bold ${accent ? "text-accent" : "text-ink"}`}>{value}</div>
      <div className="text-[0.72rem] text-ink-mute">{label}</div>
    </div>
  );
}
