import { useCaseDetail } from "../hooks/useQueries";
import { rupees, displayRootCause } from "../lib/format";
import { StatusPill } from "./ui/Pill";
import { DetailSection, KvRow } from "./detail/DetailSection";
import { AiBanner } from "./detail/AiBanner";
import { AutoClosedBanner } from "./detail/AutoClosedBanner";
import { GateChecklist } from "./detail/GateChecklist";
import { InvestigationSection } from "./detail/InvestigationSection";
import { JournalEntrySection } from "./detail/JournalEntrySection";
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
    // A tall case (real tool-call JSON, a drafted email, a full activity
    // feed, then the review form after all of it) used to have no height
    // cap at all -- `sticky top-20` alone just lets the element grow to
    // its full content height, so reaching "Take action" meant scrolling
    // the ENTIRE PAGE past everything above it. Capping this pane to the
    // viewport and giving IT the scrollbar (not the page) is the standard
    // reading-pane pattern: the case list on the left still scrolls
    // normally, but this pane stays in view and scrolls independently
    // within its own bounded box -- a short, contained scroll instead of
    // however many thousand pixels the longest section happens to be.
    <div className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl border border-border bg-surface p-6 shadow-sm">
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
        {d.sla && d.sla.sla_deadline && (
          <KvRow
            k="RBI T+5 deadline"
            v={
              <span
                className={d.sla.sla_breached ? "font-semibold text-crit" : "text-ink"}
                title={
                  d.sla.sla_breached
                    ? `Past RBI's T+5 business-day resolution bound by ${d.sla.sla_days_overdue} business day(s). RBI's TAT circular (20.09.2019) provides Rs.100/day automatic customer compensation past this point — roughly Rs.${(d.sla.sla_compensation_accrued_rupees ?? 0).toLocaleString("en-IN")} accrued so far on this case.`
                    : "Still inside RBI's T+5 business-day resolution bound."
                }
              >
                {d.sla.sla_deadline}
                {d.sla.sla_breached
                  ? ` — overdue ${d.sla.sla_days_overdue}d (≈₹${(d.sla.sla_compensation_accrued_rupees ?? 0).toLocaleString("en-IN")} accrued)`
                  : " — within SLA"}
              </span>
            }
          />
        )}
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
        <KvRow k="Confidence" v={ai.agent_confidence?.toFixed(2) ?? "—"} />
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

      <JournalEntrySection entry={d.journal_entry} />

      <DetailSection title={`Activity (${d.activity.length})`}>
        <ActivityTimeline activity={d.activity} />
      </DetailSection>

      <ReviewForm detail={d} onDone={() => {}} />
    </div>
  );
}
