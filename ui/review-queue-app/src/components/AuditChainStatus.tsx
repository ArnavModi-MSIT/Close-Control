import { useState } from "react";
import { useAuditChainVerification } from "../hooks/useQueries";

// GET /api/audit-chain/verify has existed since review_backend/chain.py
// was built -- proven under real concurrent-write load and a real tamper
// test (mutate one row, confirm it's caught, restore, confirm it's
// clean) -- but had zero UI caller until now. A reviewer had no way to
// see this mechanism exists, let alone that it currently passes.
// Deliberately re-fetched on every open, never cached client-side either
// (see useAuditChainVerification) -- an integrity check that trusts a
// cached "yes" defeats its own purpose.

export function AuditChainStatus() {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError, isFetching, refetch } = useAuditChainVerification(open);

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-3">
          <div>
            <h2 className="text-[1.05rem] font-bold text-ink">Audit Trail Integrity</h2>
            <p className="mt-0.5 text-[0.82rem] text-ink-soft">
              Every human review event is hash-chained to the one before it — altering, deleting,
              or reordering any past decision breaks every hash after it, not just its own.
            </p>
          </div>
          {data && !isFetching && (
            <span className={`shrink-0 rounded-full px-2.5 py-1 font-mono text-[0.7rem] font-bold ${
              data.intact ? "bg-good-soft text-good" : "bg-crit-soft text-crit"
            }`}>
              {data.intact ? "VERIFIED INTACT" : "TAMPERED"}
            </span>
          )}
        </div>
        <span className={`flex-shrink-0 rounded-full border border-border-2 px-3 py-1.5 font-mono text-[0.76rem] text-ink-soft transition-transform ${open ? "rotate-180" : ""}`}>
          &#9660;
        </span>
      </button>

      {open && (
        <div className="border-t border-border px-6 py-5">
          {(isLoading || isFetching) && <p className="py-6 text-center text-[0.9rem] text-ink-mute">Re-deriving every hash from scratch&hellip;</p>}

          {isError && (
            <div className="py-4">
              <p className="mb-3 text-[0.88rem] text-crit">Couldn't verify the audit chain.</p>
              <button type="button" onClick={() => refetch()}
                      className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit">
                Retry
              </button>
            </div>
          )}

          {data && !isFetching && (
            <>
              <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Rows checked" value={String(data.checked)} accent />
                <Stat label="Total review rows" value={String(data.total_rows)} />
                <Stat label="Pre-chain rows" value={String(data.pre_chain_rows)} />
                <Stat label="Broken at row" value={data.broken_at === null ? "—" : String(data.broken_at)} />
              </div>
              <p className="text-[0.83rem] text-ink-soft">
                {data.intact
                  ? `All ${data.checked} review events, re-derived from scratch just now, match their stored hash exactly. Nothing in this project's real review history has been silently altered.`
                  : `A mismatch was found at row ${data.broken_at} — every review recorded after this point can no longer be trusted as unaltered.`}
                {data.pre_chain_rows > 0 && (
                  <> {data.pre_chain_rows} row(s) predate this column and restart the chain at genesis — disclosed, not silently bridged.</>
                )}
              </p>
              <button
                type="button"
                onClick={() => refetch()}
                className="mt-3 rounded-lg border-[1.5px] border-border-2 bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-ink"
              >
                Re-verify now
              </button>
            </>
          )}
        </div>
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
