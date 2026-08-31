import { useReconciliationStatement } from "../hooks/useQueries";
import { rupees } from "../lib/format";

function Row({ label, value, count, indent, bold, muted }: {
  label: string; value: number; count?: number; indent?: boolean; bold?: boolean; muted?: boolean;
}) {
  return (
    <div className={`flex items-baseline justify-between gap-3 py-1.5 text-[0.86rem] ${indent ? "pl-4" : ""}`}>
      <span className={`${bold ? "font-semibold text-ink" : muted ? "text-ink-mute" : "text-ink-soft"}`}>
        {label}
        {count !== undefined && <span className="ml-1.5 text-[0.76rem] text-ink-mute">({count})</span>}
      </span>
      <span className={`font-mono whitespace-nowrap ${bold ? "font-bold text-ink" : "text-ink"}`}>
        {rupees(value)}
      </span>
    </div>
  );
}

function Subtotal({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-border py-2 text-[0.9rem]">
      <span className="font-semibold text-ink">{label}</span>
      <span className="font-mono font-bold text-ink">{rupees(value)}</span>
    </div>
  );
}

// Header/collapse chrome removed -- see ToolsHub.tsx, which now owns
// which one of the six tool panels is shown.
export function ReconciliationStatement() {
  const { data: stmt, isLoading, isError, refetch } = useReconciliationStatement(true);

  return (
    <div className="px-6 py-5">
      <div className="mb-4">
        <h2 className="text-[1.05rem] font-bold text-ink">Bank Reconciliation Statement</h2>
        <p className="mt-0.5 text-[0.82rem] text-ink-soft">
          Books ending balance, bridged to the bank statement ending balance &mdash; computed live,
          never from ground truth.
        </p>
      </div>

      {isLoading && <p className="py-6 text-center text-[0.9rem] text-ink-mute">Computing&hellip;</p>}

      {isError && (
        <div className="py-4">
          <p className="mb-3 text-[0.88rem] text-crit">Couldn't load the reconciliation statement.</p>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit"
          >
            Retry
          </button>
        </div>
      )}

      {stmt && (
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          {/* Books side */}
          <div className="min-w-0">
            <h3 className="mb-1 text-[0.75rem] tracking-wide text-ink-soft uppercase">
              Books side &middot; internal settlement ledger
            </h3>
            <Row label="Books ending balance" value={stmt.books_side.books_ending_balance_rupees}
                 count={stmt.books_side.captured_count} bold />
            {stmt.books_side.deductions.map((d) => (
              <Row key={d.label} label={`Less: ${d.label}`} value={-d.rupees} count={d.count} indent />
            ))}
            <Subtotal label="Expected confirmed balance" value={stmt.books_side.expected_confirmed_balance_rupees} />
            <Row label="Net fee/timing variance on confirmed items"
                 value={stmt.books_side.net_variance_on_confirmed_rupees}
                 count={stmt.books_side.net_variance_on_confirmed_count} indent />
            <Subtotal label="Adjusted confirmed balance" value={stmt.books_side.adjusted_confirmed_balance_rupees} />
            <Row label="Mixed-batch attribution (settlements with confirmed + unconfirmed members)"
                 value={stmt.books_side.mixed_settlement_adjustment_rupees}
                 count={stmt.books_side.mixed_settlement_count} indent />
            <Subtotal label="Adjusted confirmed balance (mixed-batch-aware)"
                      value={stmt.books_side.adjusted_confirmed_balance_mixed_aware_rupees} />
          </div>

          {/* Bank side */}
          <div className="min-w-0">
            <h3 className="mb-1 text-[0.75rem] tracking-wide text-ink-soft uppercase">
              Bank side &middot; bank statement, all partners
            </h3>
            <Row label="Bank statement ending balance" value={stmt.bank_side.bank_statement_ending_balance_rupees} bold />
            <Row label="Matched to confirmed settlements" value={stmt.bank_side.matched_confirmed_rupees}
                 count={stmt.bank_side.matched_confirmed_count} indent />
            <Row label="Matched, other exception types" value={stmt.bank_side.matched_other_exception_rupees}
                 count={stmt.bank_side.matched_other_exception_count} indent muted />
            <Row label="Ambiguous (safely escalated, not guessed)" value={stmt.bank_side.ambiguous_rupees}
                 count={stmt.bank_side.ambiguous_count} indent muted />
            <Row label="Orphan credits (no book entry could explain these)" value={stmt.bank_side.orphan_rupees}
                 count={stmt.bank_side.orphan_count} indent muted />
            {stmt.bank_side.orphan_rows.length > 0 && (
              <div className="mb-2 ml-8 flex flex-col gap-1 border-l-2 border-border pl-3">
                {stmt.bank_side.orphan_rows.map((r) => (
                  <div key={r.bank_txn_id} className="text-[0.74rem] text-ink-mute">
                    <span className="font-mono">{rupees(r.credit_amount_rupees)}</span>
                    {" — "}{r.narration.toLowerCase()} ({r.credit_date})
                  </div>
                ))}
              </div>
            )}
            <Row label="Unexplained (proves the partition above has no gap)"
                 value={stmt.bank_side.unexplained_rupees} count={stmt.bank_side.unexplained_count} indent muted />
          </div>

          {/* Reconciliation check, full width */}
          <div className="lg:col-span-2">
            <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border-[1.5px] px-4 py-3 ${
              stmt.reconciliation_tied
                ? "border-good/40 bg-good-soft"
                : "border-warn/40 bg-warn-soft"
            }`}>
              <div>
                <div className="text-[0.86rem] font-semibold text-ink">Reconciliation variance</div>
                <p className="mt-0.5 max-w-xl text-[0.76rem] text-ink-soft">
                  Adjusted confirmed balance (books) vs. bank credits matched to those same settlements.
                  A small residual is real, not a bug: shortage/overage-tolerance inside batched
                  settlements that also contain an unconfirmed member can't be attributed to one
                  member transaction over another.
                </p>
              </div>
              <div className="text-right">
                <div className="font-mono text-[1.15rem] font-bold text-ink">
                  {rupees(stmt.reconciliation_variance_rupees)}
                </div>
                <div className="text-[0.76rem] text-ink-soft">
                  {(Math.abs(stmt.reconciliation_variance_rupees) /
                    (stmt.bank_side.matched_confirmed_rupees || 1) * 100).toFixed(3)}% of matched confirmed
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
