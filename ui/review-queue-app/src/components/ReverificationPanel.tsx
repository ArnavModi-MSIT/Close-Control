import { useState } from "react";
import { useReverify } from "../hooks/useQueries";
import { humanizeType } from "../lib/format";
import type { ReverificationResult } from "../types";

// POST /api/reverify has existed since airflow/dags/reverification_dag.py
// was built (see review_backend/main.py) -- it re-runs the real
// deterministic matcher against the current dataset and auto-closes any
// case still awaiting human review whose transaction the matcher now
// reports as genuinely, fully clean. Against the static main demo dataset
// it always finds 0 closures (the data never changes -- expected, not a
// bug); against run_stream_simulator.py's progressively-revealed data it
// can genuinely close cases. Airflow already calls this on a 1-minute
// schedule -- this panel is the same action, reachable on demand by a
// human, not a second mechanism.
//
// Header/collapse chrome removed -- see ToolsHub.tsx, which now owns
// which one of the seven tool panels is shown.

const INLINE_CAP = 8;

function ChangedRow({ row }: { row: ReverificationResult["changed_exception"][number] }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <span className="font-mono text-[0.82rem] font-semibold text-ink">{row.transaction_id}</span>
      <span className="truncate text-[0.76rem] text-ink-mute">
        {humanizeType(row.original_exception_type)} &rarr; {humanizeType(row.current_exception_type)}
      </span>
    </div>
  );
}

export function ReverificationPanel() {
  const [preview, setPreview] = useState<ReverificationResult | null>(null);
  const [confirmed, setConfirmed] = useState<ReverificationResult | null>(null);
  const [expanded, setExpanded] = useState(false);
  const mutation = useReverify();

  const result = confirmed ?? preview;
  const canConfirm = preview !== null && !confirmed && preview.closed.length > 0;

  const runPreview = () => {
    setConfirmed(null);
    mutation.mutate(true, { onSuccess: (data) => setPreview(data) });
  };

  const runForReal = () => {
    mutation.mutate(false, { onSuccess: (data) => setConfirmed(data) });
  };

  const changed = result?.changed_exception ?? [];
  const visibleChanged = expanded ? changed : changed.slice(0, INLINE_CAP);

  return (
    <div className="px-6 py-5">
      <div className="mb-4">
        <h2 className="text-[1.05rem] font-bold text-ink">Closed-Loop Re-Verification</h2>
        <p className="mt-0.5 text-[0.82rem] text-ink-soft">
          Re-runs the deterministic matcher against the current dataset and auto-closes any
          still-open case whose transaction is now genuinely clean — the same action{" "}
          <span className="font-mono">airflow/dags/reverification_dag.py</span> already triggers
          on a schedule. Reclassification to a different, still-unreviewed exception type never
          counts as resolved and is reported separately, not closed.
        </p>
      </div>

      {mutation.isError && (
        <p className="mb-3 text-[0.88rem] text-crit">
          Couldn't reach the re-verification endpoint. Try again.
        </p>
      )}

      {!result && (
        <button
          type="button"
          onClick={runPreview}
          disabled={mutation.isPending}
          className="rounded-lg border-[1.5px] border-border-2 bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-ink disabled:opacity-60"
        >
          {mutation.isPending ? "Re-running the matcher…" : "Preview (dry run)"}
        </button>
      )}

      {result && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Cases checked" value={String(result.checked)} accent />
            <Stat label={confirmed ? "Closed" : "Would close"} value={String(result.closed.length)} />
            <Stat label="Reclassified, still open" value={String(result.changed_exception.length)} />
            <Stat label="Still open, unchanged" value={String(result.still_open.length)} />
          </div>

          <p className="mb-4 text-[0.83rem] text-ink-soft">
            {confirmed
              ? confirmed.closed.length > 0
                ? `${confirmed.closed.length} case${confirmed.closed.length > 1 ? "s" : ""} auto-closed — the underlying condition genuinely resolved. Every other still-open case was left untouched.`
                : "Nothing to close on this run — every still-open case's original condition still holds, or has changed to a different, never-reviewed exception."
              : preview && preview.closed.length > 0
                ? `${preview.closed.length} case${preview.closed.length > 1 ? "s" : ""} would auto-close if run for real. Nothing has been written yet.`
                : "This was a preview only — nothing would close on the current dataset. Against the static main demo dataset this is expected: it never changes, so there's nothing for re-verification to find."}
          </p>

          {changed.length > 0 && (
            <>
              <p className="mb-1 text-[0.72rem] font-bold uppercase tracking-wide text-ink-mute">
                Reclassified — a different, unreviewed exception, left open
              </p>
              <div className="mb-3 flex flex-col divide-y divide-border rounded-xl border border-border bg-ground-2 px-3">
                {visibleChanged.map((row) => (
                  <ChangedRow key={row.transaction_id} row={row} />
                ))}
              </div>
              {!expanded && changed.length > INLINE_CAP && (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className="mb-3 w-full py-1 text-left text-[0.78rem] font-semibold text-accent"
                >
                  + {changed.length - INLINE_CAP} more &mdash; show
                </button>
              )}
            </>
          )}

          <div className="flex flex-wrap gap-2">
            {canConfirm && (
              <button
                type="button"
                onClick={runForReal}
                disabled={mutation.isPending}
                className="rounded-lg bg-good px-3.5 py-1.5 text-[0.82rem] font-semibold text-white disabled:opacity-60"
              >
                {mutation.isPending
                  ? "Closing…"
                  : `Confirm & close ${preview!.closed.length} case${preview!.closed.length > 1 ? "s" : ""}`}
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setPreview(null);
                setConfirmed(null);
                setExpanded(false);
              }}
              className="rounded-lg border-[1.5px] border-border-2 bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-ink"
            >
              {confirmed ? "Run another preview" : "Re-run preview"}
            </button>
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
