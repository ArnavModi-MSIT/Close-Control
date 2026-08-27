import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { CaseDetail, HumanReviewDecision, ReviewerRole } from "../../types";
import { useSubmitReview } from "../../hooks/useQueries";
import { statusLabel } from "../../lib/format";
import { ApiError } from "../../api";

const OVERRIDABLE: Record<string, string> = {
  agent_exception_type: "Exception type",
  agent_root_cause: "Root cause",
  agent_recommended_action: "Recommended action",
  agent_policy_id: "Policy ID",
};

const SUBMIT_LABEL: Record<HumanReviewDecision, string> = {
  approved: "Submit approval",
  overridden: "Submit override",
  escalated: "Submit escalation",
  reverted: "Submit revert",
};

const DECISION_BTN_CLASS: Record<HumanReviewDecision, string> = {
  approved: "border-good bg-good-soft text-good",
  overridden: "border-accent bg-accent-soft text-accent",
  escalated: "border-crit bg-crit-soft text-crit",
  reverted: "border-warn bg-warn-soft text-warn",
};

export function ReviewForm({ detail, onDone }: { detail: CaseDetail; onDone: () => void }) {
  const status = detail.review_state.status;
  const mutation = useSubmitReview(detail.case.transaction_id);
  const queryClient = useQueryClient();
  const refreshCase = () =>
    queryClient.invalidateQueries({ queryKey: ["case", detail.case.transaction_id] });

  const [decision, setDecision] = useState<HumanReviewDecision | null>(null);
  const [reviewerName, setReviewerName] = useState("");
  const [reviewerRole, setReviewerRole] = useState<ReviewerRole>("analyst");
  const [notes, setNotes] = useState("");
  const [overrideField, setOverrideField] = useState<string>(Object.keys(OVERRIDABLE)[0]);
  const [overrideNewValue, setOverrideNewValue] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  if (status === "auto_resolved") {
    return (
      <RevertOnlyForm
        decision={decision}
        setDecision={setDecision}
        reviewerName={reviewerName}
        setReviewerName={setReviewerName}
        reviewerRole={reviewerRole}
        setReviewerRole={setReviewerRole}
        notes={notes}
        setNotes={setNotes}
        formError={formError}
        mutation={mutation}
        onSubmit={() => submit("reverted")}
      />
    );
  }

  const closed = status === "approved" || status === "overridden" || status === "auto_closed";

  function submit(finalDecision: HumanReviewDecision) {
    setFormError(null);
    if (!reviewerName.trim()) {
      setFormError("Enter your name first.");
      return;
    }
    const needsNotes = finalDecision === "overridden" || finalDecision === "escalated" || finalDecision === "reverted";
    if (needsNotes && !notes.trim()) {
      setFormError(
        finalDecision === "reverted"
          ? "Explain why you're reverting the AI decision first."
          : "Notes are required for an override or escalation.",
      );
      return;
    }
    const payload: Parameters<typeof mutation.mutate>[0] = {
      reviewer_name: reviewerName.trim(),
      reviewer_role: reviewerRole,
      decision: finalDecision,
      notes: notes.trim() || null,
      expected_review_count: detail.review_state.review_count,
    };
    if (finalDecision === "overridden") {
      if (!overrideNewValue.trim()) {
        setFormError("Enter a new value for the override.");
        return;
      }
      payload.override_field = overrideField;
      payload.override_old_value = String(
        (detail.ai_proposal as unknown as Record<string, unknown>)[overrideField],
      );
      payload.override_new_value = overrideNewValue.trim();
    }
    mutation.mutate(payload, { onSuccess: onDone });
  }

  return (
    <div className="mt-2 flex flex-col gap-2.5 border-t border-dashed border-border-2 pt-4">
      <h4 className="text-[0.75rem] tracking-wide text-ink-soft uppercase">Take action</h4>
      {closed && (
        <p className="text-[0.86rem] text-ink-soft">
          This case is closed ({statusLabel(status)}). Escalating opens a new review event on
          it — the closed record itself is never changed, only added to.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {(["approved", "overridden", "escalated"] as HumanReviewDecision[]).map((d) => {
          const disabled = closed && d !== "escalated";
          const active = decision === d;
          return (
            <button
              key={d}
              type="button"
              disabled={disabled}
              onClick={() => setDecision(d)}
              className={`flex-1 rounded-lg border-[1.5px] px-3.5 py-2.5 text-[0.85rem] font-semibold transition-colors ${
                disabled
                  ? "cursor-not-allowed border-border-2 bg-surface text-ink opacity-35 line-through"
                  : active
                    ? DECISION_BTN_CLASS[d]
                    : "border-border-2 bg-surface text-ink"
              }`}
            >
              {d === "approved" ? "Approve" : d === "overridden" ? "Override" : "Escalate"}
            </button>
          );
        })}
      </div>

      {decision === "overridden" && (
        <div className="flex flex-col gap-2.5 rounded-xl bg-surface-2 p-3">
          <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
            Field to override
            <select
              value={overrideField}
              onChange={(e) => setOverrideField(e.target.value)}
              className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
            >
              {Object.entries(OVERRIDABLE).map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </label>
          <div className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
            AI's current value
            <div className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink-soft">
              {String((detail.ai_proposal as unknown as Record<string, unknown>)[overrideField] ?? "—")}
            </div>
          </div>
          <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
            Override to
            <textarea
              value={overrideNewValue}
              onChange={(e) => setOverrideNewValue(e.target.value)}
              placeholder="Corrected value"
              className="min-h-16 resize-y rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
            />
          </label>
        </div>
      )}

      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Your name
        <input
          type="text"
          value={reviewerName}
          onChange={(e) => setReviewerName(e.target.value)}
          placeholder="e.g. Priya Sharma"
          className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        />
      </label>
      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Role
        <select
          value={reviewerRole}
          onChange={(e) => setReviewerRole(e.target.value as ReviewerRole)}
          className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        >
          <option value="analyst">Analyst</option>
          <option value="manager">Manager</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Notes {(decision === "overridden" || decision === "escalated") && <span className="text-crit">(required)</span>}
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Why?"
          className="min-h-16 resize-y rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        />
      </label>

      {mutation.isError && mutation.error instanceof ApiError && mutation.error.status === 409 ? (
        <div className="flex flex-col gap-2 rounded-lg bg-crit-soft px-3 py-2.5 text-[0.82rem] text-crit">
          <p>
            <strong>This case changed while you were reviewing it</strong> — someone else's action
            (a human, or automated re-verification) landed first. Your decision was NOT applied.
            Refresh the case before submitting another one.
          </p>
          <button
            type="button"
            onClick={refreshCase}
            className="self-start rounded-lg border-[1.5px] border-crit bg-surface px-3 py-1.5 text-[0.78rem] font-semibold text-crit"
          >
            Refresh case
          </button>
        </div>
      ) : (
        (formError || mutation.isError) && (
          <div className="rounded-lg bg-crit-soft px-3 py-2.5 text-[0.82rem] text-crit">
            {formError ?? (mutation.error instanceof ApiError ? mutation.error.message : "Something went wrong.")}
          </div>
        )
      )}

      <button
        type="button"
        disabled={!decision || mutation.isPending}
        onClick={() => decision && submit(decision)}
        className="rounded-lg bg-accent px-4 py-2.5 text-[0.9rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        {mutation.isPending ? "Submitting…" : decision ? SUBMIT_LABEL[decision] : "Select a decision first"}
      </button>
    </div>
  );
}

interface RevertProps {
  decision: HumanReviewDecision | null;
  setDecision: (d: HumanReviewDecision) => void;
  reviewerName: string;
  setReviewerName: (s: string) => void;
  reviewerRole: ReviewerRole;
  setReviewerRole: (r: ReviewerRole) => void;
  notes: string;
  setNotes: (s: string) => void;
  formError: string | null;
  mutation: ReturnType<typeof useSubmitReview>;
  onSubmit: () => void;
}

function RevertOnlyForm(props: RevertProps) {
  const { decision, setDecision, reviewerName, setReviewerName, reviewerRole, setReviewerRole, notes, setNotes, mutation, onSubmit } = props;
  return (
    <div className="mt-2 flex flex-col gap-2.5 border-t border-dashed border-border-2 pt-4">
      <h4 className="text-[0.75rem] tracking-wide text-ink-soft uppercase">Take action</h4>
      <p className="text-[0.86rem] text-ink-soft">
        The deterministic gate auto-resolved this without human input, based on the AI's proposal.
        Revert it if you disagree — it will move to Pending for normal review.
      </p>
      <button
        type="button"
        onClick={() => setDecision("reverted")}
        className={`w-full rounded-lg border-[1.5px] px-3.5 py-2.5 text-[0.85rem] font-semibold ${
          decision === "reverted" ? DECISION_BTN_CLASS.reverted : "border-border-2 bg-surface text-ink"
        }`}
      >
        Revert to pending
      </button>

      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Your name
        <input
          type="text"
          value={reviewerName}
          onChange={(e) => setReviewerName(e.target.value)}
          placeholder="e.g. Priya Sharma"
          className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        />
      </label>
      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Role
        <select
          value={reviewerRole}
          onChange={(e) => setReviewerRole(e.target.value as ReviewerRole)}
          className="rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        >
          <option value="analyst">Analyst</option>
          <option value="manager">Manager</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[0.78rem] text-ink-soft">
        Why are you reverting this? <span className="text-crit">(required)</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="What looks wrong about the AI decision?"
          className="min-h-16 resize-y rounded-lg border border-border-2 bg-surface px-2.5 py-2 text-[0.88rem] text-ink"
        />
      </label>

      {(props.formError || mutation.isError) && (
        <div className="rounded-lg bg-crit-soft px-3 py-2.5 text-[0.82rem] text-crit">
          {props.formError ?? (mutation.error instanceof ApiError ? mutation.error.message : "Something went wrong.")}
        </div>
      )}

      <button
        type="button"
        disabled={decision !== "reverted" || mutation.isPending}
        onClick={onSubmit}
        className="rounded-lg bg-accent px-4 py-2.5 text-[0.9rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        {mutation.isPending ? "Submitting…" : "Submit revert"}
      </button>
    </div>
  );
}
