import type { CaseListItem } from "../types";
import { StatusPill } from "./ui/Pill";
import { rupees, statusLabel, humanizeType } from "../lib/format";

interface Props {
  items: CaseListItem[];
  selected: string | null;
  onSelect: (transactionId: string) => void;
}

// A small, fixed-width glyph badge with the full explanation moved into its
// title tooltip -- the previous full-text pills ("AI AGENT INVESTIGATED",
// "SLA +10d") were what forced the Transaction column, and with it the
// whole table, wider than its panel, requiring horizontal scroll to reach
// Confidence/Status. Nothing is lost -- every badge still carries its full
// sentence on hover -- only the always-visible footprint shrinks.
function Badge({ label, title, tone }: { label: string; title: string; tone: "crit" | "accent" | "good" | "accent-soft" }) {
  const toneClass = {
    crit: "bg-crit text-white",
    accent: "bg-accent text-white",
    good: "bg-good text-white",
    "accent-soft": "bg-accent-soft text-accent",
  }[tone];
  return (
    <span title={title} className={`ml-1 inline-flex shrink-0 items-center rounded px-1 py-0.5 text-[0.62rem] font-bold leading-none ${toneClass}`}>
      {label}
    </span>
  );
}

export function CaseTable({ items, selected, onSelect }: Props) {
  if (items.length === 0) {
    return (
      <div className="p-10 text-center text-sm text-ink-mute">No cases match these filters.</div>
    );
  }

  return (
    <table className="w-full table-fixed border-collapse text-[0.85rem]">
      <colgroup>
        <col className="w-[26%]" />
        <col className="w-[24%]" />
        <col className="w-[10%]" />
        <col className="w-[16%]" />
        <col className="w-[10%]" />
        <col className="w-[14%]" />
      </colgroup>
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
                <div className="flex flex-wrap items-center gap-y-1">
                  <span className="truncate">{c.transaction_id}</span>
                  {c.sla_breached && (
                    <Badge tone="crit" label={`+${c.sla_days_overdue}d`}
                      title={`Past RBI's T+5 business-day resolution deadline by ${c.sla_days_overdue} business day(s). RBI's TAT circular puts Rs.100/day automatic compensation on the far side of that bound -- this is an overdue regulatory obligation, not just an old case.`} />
                  )}
                  {c.status === "auto_resolved" && (
                    <Badge tone="accent" label="AUTO"
                      title="The AI proposed a resolution; the deterministic gate's conditions all held, so the system auto-resolved it -- no human input yet" />
                  )}
                  {c.status === "auto_closed" && (
                    <Badge tone="good" label="RE-VERIFIED"
                      title="Closed-loop re-verification found this is no longer an exception" />
                  )}
                  {c.resolution_source === "investigator" && (
                    <Badge tone="accent" label="AGENT"
                      title="The investigation agent's own multi-step result IS this case's AI proposal" />
                  )}
                  {c.investigated && c.resolution_source !== "investigator" && (
                    <Badge tone="accent-soft" label="+INV"
                      title="A real investigation exists for this case, but it arrived after the case was first seeded -- the original single-shot proposal stays the frozen AI proposal, this is additive detail only" />
                  )}
                </div>
              </td>
              <td className="px-3.5 py-2.5 break-words">{humanizeType(c.matcher_exception_type)}</td>
              <td className="px-3.5 py-2.5">
                <span
                  className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[0.68rem] font-bold whitespace-nowrap ${
                    c.required_approval_tier === 2
                      ? "border-crit/35 text-crit"
                      : "border-border-2 text-ink-soft"
                  }`}
                >
                  T{c.required_approval_tier}
                </span>
              </td>
              <td className="px-3.5 py-2.5 text-right font-mono break-words">{rupees(c.amount_at_risk_rupees)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono">{c.agent_confidence?.toFixed(2) ?? "—"}</td>
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
