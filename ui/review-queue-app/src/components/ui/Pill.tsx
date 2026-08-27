import type { CaseStatus } from "../../types";
import { statusLabel } from "../../lib/format";

const STYLES: Record<CaseStatus, string> = {
  auto_resolved: "bg-accent text-white",
  pending: "bg-surface-2 text-ink-soft border border-border-2",
  pending_manager_approval: "bg-warn-soft text-warn",
  approved: "bg-good-soft text-good",
  overridden: "bg-accent-soft text-accent",
  escalated: "bg-crit-soft text-crit",
  auto_closed: "bg-good-soft text-good",
};

const DOT: Record<CaseStatus, string> = {
  auto_resolved: "bg-white",
  pending: "bg-ink-mute",
  pending_manager_approval: "bg-warn",
  approved: "bg-good",
  overridden: "bg-accent",
  escalated: "bg-crit",
  auto_closed: "bg-good",
};

export function StatusPill({ status }: { status: CaseStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[0.7rem] font-semibold whitespace-nowrap ${STYLES[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[status]}`} />
      {statusLabel(status)}
    </span>
  );
}
