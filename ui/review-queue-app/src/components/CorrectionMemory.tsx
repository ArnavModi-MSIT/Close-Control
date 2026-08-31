import { useState } from "react";
import { useCorrections } from "../hooks/useQueries";
import { humanizeType } from "../lib/format";
import type { Correction } from "../types";

// corrections.py's correction memory was write-only from this UI's own
// point of view until now -- a human override gets appended to
// data/correction_log.jsonl and genuinely feeds back into FUTURE agent/
// investigator prompts (see agent/client.py, investigator/loop.py), but
// nothing ever showed a reviewer that this mechanism exists, let alone
// what's actually on file. Read-only, same as MatcherAutoResolved --
// nothing here is reviewable, it's evidence the mechanism is real.

function CorrectionRow({ c }: { c: Correction }) {
  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center gap-2 text-[0.82rem]">
        <span className="font-mono font-semibold text-ink">{c.transaction_id}</span>
        <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[0.7rem] font-bold text-accent">
          {c.override_field}
        </span>
        <span className="text-ink-mute">by {c.reviewer_name}</span>
      </div>
      <div className="mt-1.5 text-[0.8rem] leading-relaxed">
        <span className="text-crit line-through decoration-1">{c.override_old_value}</span>
        <span className="mx-1.5 text-ink-mute">&rarr;</span>
        <span className="font-medium text-good">{c.override_new_value}</span>
      </div>
      <p className="mt-1 text-[0.78rem] italic text-ink-soft">&ldquo;{c.reason}&rdquo;</p>
    </div>
  );
}

export function CorrectionMemory() {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError, refetch } = useCorrections(open);

  return (
    <div className="rounded-2xl border border-border bg-surface shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
        aria-expanded={open}
      >
        <div>
          <h2 className="text-[1.05rem] font-bold text-ink">Correction Memory</h2>
          <p className="mt-0.5 text-[0.82rem] text-ink-soft">
            Past human overrides, fed back into future AI proposals of the same exception type —
            the AI never repeats a correction a reviewer already made once.
          </p>
        </div>
        <span className={`flex-shrink-0 rounded-full border border-border-2 px-3 py-1.5 font-mono text-[0.76rem] text-ink-soft transition-transform ${open ? "rotate-180" : ""}`}>
          &#9660;
        </span>
      </button>

      {open && (
        <div className="border-t border-border px-6 py-5">
          {isLoading && <p className="py-6 text-center text-[0.9rem] text-ink-mute">Loading&hellip;</p>}

          {isError && (
            <div className="py-4">
              <p className="mb-3 text-[0.88rem] text-crit">Couldn't load correction memory.</p>
              <button type="button" onClick={() => refetch()}
                      className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit">
                Retry
              </button>
            </div>
          )}

          {data && data.total_corrections === 0 && (
            <p className="py-4 text-center text-[0.85rem] text-ink-mute">
              No corrections on file yet — this fills in the first time a reviewer overrides an
              AI proposal.
            </p>
          )}

          {data && data.total_corrections > 0 && (
            <div className="flex flex-col divide-y divide-border">
              {Object.entries(data.by_exception_type).map(([type, items]) => (
                <div key={type} className="py-3 first:pt-0">
                  <div className="mb-1 text-[0.7rem] font-bold tracking-wide text-ink-mute uppercase">
                    {humanizeType(type)}
                  </div>
                  <div className="flex flex-col divide-y divide-border">
                    {items.map((c, i) => <CorrectionRow key={`${c.transaction_id}-${i}`} c={c} />)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
