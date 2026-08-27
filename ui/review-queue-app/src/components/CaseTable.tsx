import type { CaseListItem } from "../types";
import { StatusPill } from "./ui/Pill";
import { rupees, statusLabel } from "../lib/format";

interface Props {
  items: CaseListItem[];
  selected: string | null;
  onSelect: (transactionId: string) => void;
}

export function CaseTable({ items, selected, onSelect }: Props) {
  if (items.length === 0) {
    return (
      <div className="p-10 text-center text-sm text-ink-mute">No cases match these filters.</div>
    );
  }

  return (
    <table className="w-full min-w-[640px] border-collapse text-[0.85rem]">
      <thead>
        <tr>
          {["Transaction", "Exception type", "Tier", "Amount at risk", "Confidence", "Status"].map((h, i) => (
            <th
              key={h}
              className={`sticky top-0 border-b border-border bg-surface px-3.5 py-3 text-[0.7rem] tracking-wide text-ink-soft uppercase ${
                i >= 3 ? "text-right" : "text-left"
              }`}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {items.map((c) => {
          const isSelected = c.transaction_id === selected;
          const investigationLabel =
            c.resolution_source === "investigator" ? ", AI agent investigated"
            : c.investigated ? ", also investigated" : "";
          const label =
            `${c.transaction_id}, ${c.matcher_exception_type}, ${rupees(c.amount_at_risk_rupees)}, ` +
            `${statusLabel(c.status)}${investigationLabel}. Press Enter to review.`;
          return (
            <tr
              key={c.transaction_id}
              tabIndex={0}
              role="button"
              aria-label={label}
              onClick={() => onSelect(c.transaction_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(c.transaction_id);
                }
              }}
              className={`cursor-pointer border-b border-border transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none ${
                isSelected ? "bg-accent-soft" : ""
              }`}
            >
              <td className="px-3.5 py-2.5 font-mono">
                {c.transaction_id}
                {c.status === "auto_resolved" && (
                  <span
                    title="The AI proposed a resolution; the deterministic gate's six conditions all held, so the system auto-resolved it -- no human input yet"
                    className="ml-1.5 rounded-full bg-accent px-2 py-0.5 text-[0.65rem] font-semibold text-white"
                  >
                    AUTO-RESOLVED
                  </span>
                )}
                {c.status === "auto_closed" && (
                  <span
                    title="Closed-loop re-verification found this is no longer an exception"
                    className="ml-1.5 rounded-full bg-good px-2 py-0.5 text-[0.65rem] font-semibold text-white"
                  >
                    AUTO-CLOSED
                  </span>
                )}
                {c.resolution_source === "investigator" && (
                  <span
                    title="The investigation agent's own multi-step result IS this case's AI proposal"
                    className="ml-1.5 rounded-full bg-accent px-2 py-0.5 text-[0.65rem] font-semibold text-white"
                  >
                    AI AGENT INVESTIGATED
                  </span>
                )}
                {c.investigated && c.resolution_source !== "investigator" && (
                  <span
                    title="A real investigation exists for this case, but it arrived after the case was first seeded -- the original single-shot proposal stays the frozen AI proposal, this is additive detail only"
                    className="ml-1.5 rounded-full bg-accent-soft px-2 py-0.5 text-[0.65rem] font-semibold text-accent"
                  >
                    ALSO INVESTIGATED
                  </span>
                )}
              </td>
              <td className="px-3.5 py-2.5">{c.matcher_exception_type}</td>
              <td className="px-3.5 py-2.5">
                <span
                  className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[0.68rem] font-bold whitespace-nowrap ${
                    c.required_approval_tier === 2
                      ? "border-crit/35 text-crit"
                      : "border-border-2 text-ink-soft"
                  }`}
                >
                  TIER {c.required_approval_tier}
                </span>
              </td>
              <td className="px-3.5 py-2.5 text-right font-mono">{rupees(c.amount_at_risk_rupees)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono">{c.agent_confidence.toFixed(2)}</td>
              <td className="px-3.5 py-2.5">
                <StatusPill status={c.status} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
