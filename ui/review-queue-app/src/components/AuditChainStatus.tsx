import { useState } from "react";
import { useAuditChainVerification } from "../hooks/useQueries";
import { humanizeType } from "../lib/format";
import type { AuditChainRow } from "../types";

// GET /api/audit-chain/verify has existed since review_backend/chain.py
// was built -- proven under real concurrent-write load and a real tamper
// test (mutate one row, confirm it's caught, restore, confirm it's
// clean). verify_chain() walks and hashes every row to produce these
// numbers; the row detail below was always being computed, just never
// returned or rendered.
//
// Header/collapse chrome removed -- see ToolsHub.tsx, which now owns
// which one of the seven tool panels is shown.

const INLINE_CAP = 8;

function RowItem({ row }: { row: AuditChainRow }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[0.82rem]">
          <span className="font-mono font-semibold text-ink">{row.transaction_id}</span>
          <span className="text-ink-mute">{humanizeType(row.decision)}</span>
        </div>
        <div className="truncate text-[0.74rem] text-ink-mute">
          {row.reviewer_name} &middot; {new Date(row.created_at).toLocaleString()}
        </div>
      </div>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[0.68rem] font-bold ${
          row.verified === null
            ? "bg-ground-2 text-ink-mute"
            : row.verified
              ? "bg-good-soft text-good"
              : "bg-crit-soft text-crit"
        }`}
        title={row.verified === null ? "Predates the hash-chain column -- no hash to check" : undefined}
      >
        {row.verified === null ? "PRE-CHAIN" : row.verified ? "✓ VERIFIED" : "✗ TAMPERED"}
      </span>
    </div>
  );
}

export function AuditChainStatus() {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, isError, isFetching, refetch } = useAuditChainVerification(true);

  const rows = data?.rows ?? [];
  // Most recent first -- the newest review is the most interesting one to
  // see without scrolling, and it's also the one a viewer just watched
  // happen if this follows a real override in a demo.
  const ordered = [...rows].reverse();
  const visible = expanded ? ordered : ordered.slice(0, INLINE_CAP);

  return (
    <div className="px-6 py-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
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
            <Stat label="Broken at row" value={data.broken_at === null ? "—" : `#${data.broken_at.id}`} />
          </div>
          <p className="mb-4 text-[0.83rem] text-ink-soft">
            {data.intact
              ? `All ${data.checked} review events, re-derived from scratch just now, match their stored hash exactly. Nothing in this project's real review history has been silently altered.`
              : `A mismatch was found at row #${data.broken_at?.id} (${data.broken_at?.transaction_id}) — every review recorded after this point can no longer be trusted as unaltered.`}
            {data.pre_chain_rows > 0 && (
              <> {data.pre_chain_rows} row(s) predate this column and restart the chain at genesis — disclosed, not silently bridged.</>
            )}
          </p>

          {rows.length > 0 && (
            <div className="flex flex-col divide-y divide-border rounded-xl border border-border bg-ground-2 px-3">
              {visible.map((r) => <RowItem key={r.id} row={r} />)}
            </div>
          )}
          {!expanded && ordered.length > INLINE_CAP && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-2 w-full py-2 text-left text-[0.78rem] font-semibold text-accent"
            >
              + {ordered.length - INLINE_CAP} more review event{ordered.length - INLINE_CAP > 1 ? "s" : ""} &mdash; show
            </button>
          )}

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
