import { useState, type ComponentType } from "react";
import { QAPanel } from "./QAPanel";
import { ReconciliationStatement } from "./ReconciliationStatement";
import { RootCauseClusters } from "./RootCauseClusters";
import { MatcherAutoResolved } from "./MatcherAutoResolved";
import { CorrectionMemory } from "./CorrectionMemory";
import { AuditChainStatus } from "./AuditChainStatus";

// Replaces what used to be six full-width collapsible cards stacked
// vertically -- a reviewer had to scroll past five closed headers to
// reach the sixth. Same six tools, same components (each stripped of its
// own collapse chrome -- see the "Header/collapse chrome removed" note
// atop each), now addressed by a sidebar tab list: exactly one panel
// mounted in the content pane at a time, which is also why each panel's
// query hooks can now just pass `true` unconditionally for `enabled` --
// mounting IS the open signal.

type ToolId = "qa" | "reconciliation" | "root-cause" | "matcher" | "corrections" | "audit";
type Accent = "accent" | "good" | "warn";

const TOOLS: { id: ToolId; label: string; description: string; accent: Accent; Panel: ComponentType }[] = [
  {
    id: "qa",
    label: "Settlement Q&A",
    description: "Ask a question about the reconciliation data — grounded in real tool calls.",
    accent: "accent",
    Panel: QAPanel,
  },
  {
    id: "reconciliation",
    label: "Bank Reconciliation Statement",
    description: "Books ending balance, bridged to the bank statement — computed live.",
    accent: "good",
    Panel: ReconciliationStatement,
  },
  {
    id: "root-cause",
    label: "Root-Cause Clusters",
    description: "The escalated queue, collapsed into its underlying causes.",
    accent: "warn",
    Panel: RootCauseClusters,
  },
  {
    id: "matcher",
    label: "Matcher-Auto-Resolved",
    description: "Closed by the deterministic matcher alone, zero LLM involved.",
    accent: "accent",
    Panel: MatcherAutoResolved,
  },
  {
    id: "corrections",
    label: "Correction Memory",
    description: "Past human overrides, fed back into future AI proposals.",
    accent: "good",
    Panel: CorrectionMemory,
  },
  {
    id: "audit",
    label: "Audit Trail Integrity",
    description: "Every review event hash-chained to the one before it.",
    accent: "warn",
    Panel: AuditChainStatus,
  },
];

const ACCENT_BAR: Record<Accent, string> = {
  accent: "border-l-accent",
  good: "border-l-good",
  warn: "border-l-warn",
};
const ACCENT_TEXT: Record<Accent, string> = {
  accent: "text-accent",
  good: "text-good",
  warn: "text-warn",
};
const ACCENT_DOT: Record<Accent, string> = {
  accent: "bg-accent",
  good: "bg-good",
  warn: "bg-warn",
};

export function ToolsHub() {
  const [active, setActive] = useState<ToolId | null>(null);
  const activeTool = TOOLS.find((t) => t.id === active) ?? null;
  const ActivePanel = activeTool?.Panel;

  return (
    <div className="mb-6">
      <h2 className="mb-3 text-[1.05rem] font-bold text-ink">AI &amp; audit tools</h2>
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[300px_1fr]">
        <nav
          aria-label="Tool panels"
          className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0"
        >
          {TOOLS.map((t) => {
            const isActive = t.id === active;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setActive(isActive ? null : t.id)}
                aria-pressed={isActive}
                className={`min-w-[230px] shrink-0 rounded-xl border-[1.5px] border-l-4 bg-surface px-4 py-3 text-left shadow-sm transition-colors lg:min-w-0 lg:shrink ${
                  isActive ? `${ACCENT_BAR[t.accent]} border-border-2 bg-ground-2` : "border-l-transparent border-border"
                }`}
              >
                <div className={`flex items-center gap-2 text-[0.85rem] font-bold ${isActive ? ACCENT_TEXT[t.accent] : "text-ink"}`}>
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${ACCENT_DOT[t.accent]}`} />
                  {t.label}
                </div>
                <p className="mt-1 hidden text-[0.76rem] leading-snug text-ink-mute sm:block">
                  {t.description}
                </p>
              </button>
            );
          })}
        </nav>

        <div className="min-w-0 rounded-2xl border border-border bg-surface shadow-sm">
          {ActivePanel ? (
            <ActivePanel />
          ) : (
            <div className="flex min-h-[220px] flex-col items-center justify-center gap-1.5 px-6 py-10 text-center">
              <p className="text-[0.92rem] font-semibold text-ink">Pick a tool on the left</p>
              <p className="max-w-sm text-[0.8rem] text-ink-mute">
                Six deterministic and AI-assisted views sit behind this queue &mdash; Q&amp;A,
                bank reconciliation, root-cause clustering, matcher auto-resolves, correction
                memory, and the hash-chained audit trail.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
