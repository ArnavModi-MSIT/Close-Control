import { useCaseDetail } from "../hooks/useQueries";
import { rupees, displayRootCause } from "../lib/format";
import { StatusPill } from "./ui/Pill";
import { DetailSection, KvRow } from "./detail/DetailSection";
import { AiBanner } from "./detail/AiBanner";
import { AutoClosedBanner } from "./detail/AutoClosedBanner";
import { GateChecklist } from "./detail/GateChecklist";
import { InvestigationSection } from "./detail/InvestigationSection";
import { ActivityTimeline } from "./detail/ActivityTimeline";
import { ReviewForm } from "./detail/ReviewForm";

export function DetailPanel({ transactionId }: { transactionId: string | null }) {
  const { data: d, isLoading, isError, refetch } = useCaseDetail(transactionId);

  if (!transactionId) {
    return (
      <div className="sticky top-20 rounded-2xl border border-border bg-surface p-6 py-16 text-center text-[0.9rem] text-ink-mute shadow-sm">
        Select a case from the table to review it.
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="sticky top-20 rounded-2xl border border-border bg-surface p-6 py-16 text-center text-[0.9rem] text-ink-mute shadow-sm">
        Loading…
      </div>
    );
  }

  if (isError || !d) {
    return (
      <div className="sticky top-20 rounded-2xl border border-border bg-surface p-6 shadow-sm">
        <p className="mb-3 text-[0.9rem] text-crit">Couldn't load details for {transactionId}.</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit"
        >
          Retry
        </button>
      </div>
    );
  }

  const { case: c, ai_proposal: ai, gate, evidence: ev, review_state: rs } = d;

  return (
    <div className="sticky top-20 rounded-2xl border border-border bg-surface p-6 shadow-sm">
      {rs.status === "auto_resolved" && <AiBanner />}
      {rs.status === "auto_closed" && <AutoClosedBanner />}

      <DetailSection title={`Case ${c.transaction_id}`}>
        <KvRow k="Status" v={<StatusPill status={rs.status} />} />
        <KvRow
          k="Approval tier"
          v={`Tier ${c.required_approval_tier}${rs.awaiting_role ? ` — awaiting ${rs.awaiting_role}` : ""}`}
        />
        <KvRow k="Amount at risk" v={rupees(c.amount_at_risk_rupees)} />
        <KvRow k="Merchant" v={c.merchant_id} />
        <KvRow
          k="Exception type (matcher)"
          v={
            <span title="The deterministic matcher's own exception_type — authoritative for policy lookup and the auto-resolve allowlist, regardless of how the AI proposal below reclassifies it.">
              {c.matcher_exception_type}
            </span>
          }
        />
        {ev.all_signals.length > 1 && (
          <KvRow
            k="Also observed"
            v={
              <span title="Every co-occurring signal the matcher detected on this case — only the highest-priority one above became the routing decision (matching/report.py's EXCEPTION_PRIORITY), but the others are real, not discarded.">
                {ev.all_signals.filter((s) => s !== c.matcher_exception_type).join(", ")}
              </span>
            }
          />
        )}
      </DetailSection>

      <DetailSection
        title="AI proposal"
        badge={
          ai.resolution_source === "investigator" ? (
            <span
              className="rounded-full bg-accent px-2 py-0.5 font-mono text-[0.66rem] font-semibold tracking-wide text-white normal-case"
              title="This proposal came from the multi-step tool-using investigation agent (investigator/), not the single-shot classifier -- it had real tools and gathered its own evidence. See the Investigation section below for its full trace."
            >
              investigation agent
            </span>
          ) : ai.provider === "mock" && (
            <span
              className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-[0.66rem] font-semibold tracking-wide text-ink-mute normal-case"
              title="This case's original proposal came from the $0 deterministic mock provider, not a live LLM call -- see run_agent.py --mode mock. If it also has a real Investigation section below, that one did use a live model but arrived after this case was first seeded, so it's additive detail, not the primary proposal."
            >
              mock provider
            </span>
          )
        }
      >
        <KvRow k="Exception type" v={ai.agent_exception_type + (ai.reclassified ? " (reclassified)" : "")} />
        <KvRow
          k="Policy"
          v={ai.agent_policy_id + (ai.policy_id_consistent ? " ✓" : " ✗ mismatch")}
        />
        <KvRow k="Confidence" v={ai.agent_confidence.toFixed(2)} />
        <KvRow k="Sufficient evidence?" v={ai.agent_sufficient_evidence ? "Yes" : "No"} />
        <p className="mt-2.5 text-[0.86rem] text-ink-soft">{displayRootCause(ai.agent_root_cause)}</p>
        <p className="text-[0.86rem] text-ink-soft"><strong>Recommended:</strong> {ai.agent_recommended_action}</p>
      </DetailSection>

      <DetailSection title="Gate reasoning">
        {gate.condition_checks ? (
          <GateChecklist checks={gate.condition_checks} />
        ) : (
          <>
            <ul className="flex flex-col gap-1.5">
              {gate.reasons.map((r, i) => (
                <li key={i} className="rounded-lg border border-border bg-surface-2 px-2.5 py-2 text-[0.83rem]">{r}</li>
              ))}
            </ul>
            <p className="mt-2 text-[0.76rem] text-ink-mute">
              Structured per-condition breakdown isn't available for this case — it was seeded
              before that field existed.
            </p>
          </>
        )}
      </DetailSection>

      <DetailSection title="Evidence cited">
        <ul className="flex flex-col gap-1.5">
          {ev.fields_cited.map((f, i) => (
            <li key={i} className="rounded-lg border border-border bg-surface-2 px-2.5 py-2 text-[0.83rem]">
              <span className="mb-0.5 block font-mono text-[0.72rem] text-accent">{f.field}</span>
              {f.note ? (
                <span className="text-ink-mute">({f.note})</span>
              ) : typeof f.value === "object" && f.value !== null ? (
                <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap font-mono text-[0.76rem]">
                  {JSON.stringify(f.value, null, 2)}
                </pre>
              ) : (
                String(f.value)
              )}
            </li>
          ))}
        </ul>
      </DetailSection>

      {d.investigation && (
        <InvestigationSection inv={d.investigation} isCasePrimary={ai.resolution_source === "investigator"} />
      )}

      <DetailSection title={`Activity (${d.activity.length})`}>
        <ActivityTimeline activity={d.activity} />
      </DetailSection>

      <ReviewForm detail={d} onDone={() => {}} />
    </div>
  );
}
