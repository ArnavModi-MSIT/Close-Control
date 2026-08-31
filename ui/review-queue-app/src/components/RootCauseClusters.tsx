import { useState } from "react";
import { useRootCauseClusters, useBulkReview, useRunSummary } from "../hooks/useQueries";
import { rupees, humanizeType, isMockText, stripMockBracket } from "../lib/format";
import { ApiError } from "../api";
import type { BulkReviewCaseResult, BulkReviewDecision, RootCauseCluster } from "../types";

const RISK_DOT: Record<string, string> = {
  high: "bg-crit", medium: "bg-warn", low: "bg-good", none: "bg-ink-mute",
};

// clusters arrive from the API already sorted by case_count desc (the
// biggest lever first) -- but on this dataset one exception type
// (missing_bank_reference) genuinely dominates the fan-out (23.67x vs.
// 1.0-1.8x for everything else, see matching/root_cause.py), so a flat
// top-N list is a wall of near-identical rows before any other type ever
// appears. Grouping by type and capping each group's INLINE rows fixes
// that without hiding anything -- every cluster is still reachable, just
// behind one "show more" per type instead of scrolling past twenty
// lookalikes. Map preserves insertion order, so a group's position is set
// by wherever its FIRST (highest-ranked) cluster would have sorted.
function groupByType(clusters: RootCauseCluster[]): [string, RootCauseCluster[]][] {
  const map = new Map<string, RootCauseCluster[]>();
  for (const c of clusters) {
    const group = map.get(c.final_exception_type);
    if (group) group.push(c);
    else map.set(c.final_exception_type, [c]);
  }
  return [...map.entries()];
}

const INLINE_CAP_PER_TYPE = 4;

function ClusterRow({ cluster, onReview }: { cluster: RootCauseCluster; onReview: (c: RootCauseCluster) => void }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={`h-2 w-2 shrink-0 rounded-full ${RISK_DOT[cluster.risk_class] ?? "bg-ink-mute"}`} />
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[0.85rem] font-semibold text-ink">
            {humanizeType(cluster.final_exception_type)}
            {cluster.case_count > 1 && (
              <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[0.72rem] font-bold text-accent">
                &times;{cluster.case_count}
              </span>
            )}
          </div>
          <div className="truncate text-[0.76rem] text-ink-mute">
            {cluster.cluster_basis === "settlement"
              ? `settlement ${cluster.settlement_id}`
              : `merchant ${cluster.merchant_id} (no shared settlement)`}
            {" · "}{rupees(cluster.amount_at_risk_rupees)} at risk
          </div>
        </div>
      </div>
      {cluster.case_count > 1 && (
        <button
          type="button"
          onClick={() => onReview(cluster)}
          className="shrink-0 rounded-lg border-[1.5px] border-accent bg-accent-soft px-3 py-1.5 text-[0.78rem] font-semibold text-accent"
        >
          Review cluster
        </button>
      )}
    </div>
  );
}

function ClusterTypeGroup({ type, clusters, onReview }: {
  type: string; clusters: RootCauseCluster[]; onReview: (c: RootCauseCluster) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? clusters : clusters.slice(0, INLINE_CAP_PER_TYPE);
  const hidden = clusters.slice(INLINE_CAP_PER_TYPE);
  const hiddenCases = hidden.reduce((sum, c) => sum + c.case_count, 0);

  return (
    <>
      {visible.map((c) => <ClusterRow key={c.cluster_id} cluster={c} onReview={onReview} />)}
      {!expanded && hidden.length > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="w-full py-2 text-left text-[0.78rem] font-semibold text-accent"
        >
          + {hidden.length} more {humanizeType(type)} settlement{hidden.length > 1 ? "s" : ""}
          {" "}({hiddenCases} more case{hiddenCases > 1 ? "s" : ""}) &mdash; show
        </button>
      )}
    </>
  );
}

function BulkReviewDialog({ cluster, onClose }: { cluster: RootCauseCluster; onClose: () => void }) {
  const mutation = useBulkReview();
  const [decision, setDecision] = useState<BulkReviewDecision>("approved");
  const [reviewerName, setReviewerName] = useState("");
  const [reviewerRole, setReviewerRole] = useState<"analyst" | "manager">("analyst");
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkReviewCaseResult[] | null>(null);

  function submit() {
    setFormError(null);
    if (!reviewerName.trim()) { setFormError("Enter your name first."); return; }
    if (decision === "escalated" && !notes.trim()) {
      setFormError("Escalating requires a reason in notes.");
      return;
    }
    mutation.mutate(
      {
        transaction_ids: cluster.transaction_ids,
        reviewer_name: reviewerName.trim(),
        reviewer_role: reviewerRole,
        decision,
        notes: notes.trim() || null,
      },
      {
        onSuccess: (res) => setResult(res.results),
        onError: (err) =>
          setFormError(err instanceof ApiError ? err.message : "Bulk review failed."),
      },
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-surface p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {!result ? (
          <>
            <h3 className="text-[1.05rem] font-bold text-ink">
              Review {cluster.case_count} cases as one cluster
            </h3>
            <p className="mt-1 text-[0.82rem] text-ink-soft">
              All {cluster.case_count} cases trace to the same {cluster.cluster_basis === "settlement"
                ? `settlement (${cluster.settlement_id})` : `merchant`}, classified{" "}
              <span className="font-semibold text-ink">{cluster.final_exception_type}</span>, totaling{" "}
              <span className="font-mono font-semibold text-ink">{rupees(cluster.amount_at_risk_rupees)}</span>{" "}
              at risk. Each case still validates independently against its own tier and current
              status &mdash; a case someone else has already acted on is skipped, not overwritten.
            </p>

            <div className="mt-4 flex flex-col gap-2.5">
              <div className="flex gap-2">
                {(["approved", "escalated"] as BulkReviewDecision[]).map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setDecision(d)}
                    className={`flex-1 rounded-lg border-[1.5px] px-3.5 py-2.5 text-[0.85rem] font-semibold ${
                      decision === d
                        ? d === "approved"
                          ? "border-good bg-good-soft text-good"
                          : "border-crit bg-crit-soft text-crit"
                        : "border-border-2 bg-surface text-ink"
                    }`}
                  >
                    {d === "approved" ? "Approve all" : "Escalate all"}
                  </button>
                ))}
              </div>

              <input
                value={reviewerName}
                onChange={(e) => setReviewerName(e.target.value)}
                placeholder="Your name"
                className="rounded-lg border border-border-2 bg-surface px-3 py-2 text-[0.85rem] text-ink"
              />
              <select
                value={reviewerRole}
                onChange={(e) => setReviewerRole(e.target.value as "analyst" | "manager")}
                className="rounded-lg border border-border-2 bg-surface px-3 py-2 text-[0.85rem] text-ink"
              >
                <option value="analyst">Analyst</option>
                <option value="manager">Manager</option>
              </select>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder={decision === "escalated" ? "Reason for escalation (required)" : "Notes (optional)"}
                rows={2}
                className="rounded-lg border border-border-2 bg-surface px-3 py-2 text-[0.85rem] text-ink"
              />

              {formError && <p className="text-[0.8rem] text-crit">{formError}</p>}

              <div className="flex justify-end gap-2 pt-1">
                <button type="button" onClick={onClose}
                        className="rounded-lg border-[1.5px] border-border-2 px-3.5 py-2 text-[0.85rem] font-semibold text-ink">
                  Cancel
                </button>
                <button type="button" onClick={submit} disabled={mutation.isPending}
                        className="rounded-lg border-[1.5px] border-accent bg-accent-soft px-3.5 py-2 text-[0.85rem] font-semibold text-accent disabled:opacity-50">
                  {mutation.isPending ? "Submitting…" : `${decision === "approved" ? "Approve" : "Escalate"} ${cluster.case_count} cases`}
                </button>
              </div>
            </div>
          </>
        ) : (
          <>
            <h3 className="text-[1.05rem] font-bold text-ink">Cluster review complete</h3>
            <p className="mt-1 text-[0.82rem] text-ink-soft">
              {result.filter((r) => r.outcome === "reviewed").length} reviewed,{" "}
              {result.filter((r) => r.outcome === "skipped").length} skipped.
            </p>
            <div className="mt-3 flex max-h-64 flex-col gap-1 overflow-y-auto">
              {result.map((r) => (
                <div key={r.transaction_id} className="flex items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-1.5 text-[0.78rem]">
                  <span className="font-mono text-ink">{r.transaction_id}</span>
                  <span className={r.outcome === "reviewed" ? "text-good" : "text-ink-mute"}>
                    {r.outcome === "reviewed" ? r.new_status : r.reason}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={onClose}
                      className="rounded-lg border-[1.5px] border-border-2 px-3.5 py-2 text-[0.85rem] font-semibold text-ink">
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// Header/collapse chrome removed -- see ToolsHub.tsx, which now owns
// which one of the six tool panels is shown.
export function RootCauseClusters() {
  const { data, isLoading, isError, refetch } = useRootCauseClusters(true);
  const { data: runSummary } = useRunSummary(true);
  const [reviewing, setReviewing] = useState<RootCauseCluster | null>(null);

  return (
    <div className="px-6 py-5">
      <div className="mb-4">
        <h2 className="text-[1.05rem] font-bold text-ink">Root-Cause Clusters</h2>
        <p className="mt-0.5 text-[0.82rem] text-ink-soft">
          The escalated queue collapsed into its underlying causes &mdash; one settlement's missing
          reference can flag dozens of cases at once.
        </p>
      </div>

      {isLoading && <p className="py-6 text-center text-[0.9rem] text-ink-mute">Clustering&hellip;</p>}

      {isError && (
        <div className="py-4">
          <p className="mb-3 text-[0.88rem] text-crit">Couldn't load root-cause clusters.</p>
          <button type="button" onClick={() => refetch()}
                  className="rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-[0.82rem] font-semibold text-crit">
            Retry
          </button>
        </div>
      )}

      {data && (
        <>
          {runSummary?.generated && runSummary.summary && (
            <div className="mb-4 rounded-xl border border-border bg-ground-2 px-4 py-3">
              <div className="mb-1 flex items-center gap-2 text-[0.7rem] tracking-wide text-ink-soft uppercase">
                Run summary
                {isMockText(runSummary.summary) && (
                  <span className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-[0.62rem] font-semibold text-ink-mute normal-case"
                        title="A deterministic template narrated this from the real numbers above -- not a live LLM call. See run_summary.py --provider ollama for a real narrated version.">
                    mock
                  </span>
                )}
              </div>
              <p className="text-[0.85rem] text-ink-soft">{stripMockBracket(runSummary.summary)}</p>
            </div>
          )}
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Escalated cases" value={String(data.summary.escalated_cases)} />
            <Stat label="Root causes" value={String(data.summary.root_cause_clusters)} accent />
            <Stat label="Amplification" value={`${data.summary.amplification_factor}x`} />
            <Stat label="Covered by fan-out"
                  value={`${data.summary.pct_cases_in_multi_case_clusters}%`} />
          </div>

          <div className="flex flex-col divide-y divide-border">
            {groupByType(data.clusters).map(([type, group]) => (
              <ClusterTypeGroup key={type} type={type} clusters={group} onReview={setReviewing} />
            ))}
          </div>
        </>
      )}

      {reviewing && <BulkReviewDialog cluster={reviewing} onClose={() => setReviewing(null)} />}
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
