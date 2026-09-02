import { useState, type ComponentType } from "react";
import { QAPanel } from "./QAPanel";
import { ReconciliationStatement } from "./ReconciliationStatement";
import { RootCauseClusters } from "./RootCauseClusters";
import { MatcherAutoResolved } from "./MatcherAutoResolved";
import { CorrectionMemory } from "./CorrectionMemory";
import { AuditChainStatus } from "./AuditChainStatus";
import { ReverificationPanel } from "./ReverificationPanel";

// Replaces what used to be six full-width collapsible cards stacked
// vertically -- a reviewer had to scroll past five closed headers to
// reach the sixth. Same idea, same per-panel components (each stripped of
// its own collapse chrome -- see the "Header/collapse chrome removed"
// note atop each), now addressed by a sidebar tab list. A seventh tool
// (re-verification) joined after an audit found POST /api/reverify had no
// dashboard caller at all -- Airflow could trigger it, a human never could.
//
// A panel mounts the FIRST time its tab is opened and then stays mounted
// for the rest of the page's life -- switching tabs only toggles CSS
// visibility (see `hidden` below), it never unmounts anything. This is
// deliberate, not incidental: QAPanel's answer and ReverificationPanel's
// preview/confirm state both live in local useState/useMutation, which
// React throws away on unmount -- a naive "only render the active panel"
// approach silently discarded a real ~85s Ollama answer the moment a
// reviewer glanced at another tab. `visited` tracks which tools have ever
// been opened so a tool nobody clicked yet still costs zero network
// requests -- switching tabs was never meant to become "eagerly fetch
// all seven panels on page load."

type ToolId = "qa" | "reconciliation" | "root-cause" | "matcher" | "corrections" | "audit" | "reverify";
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
  {
    id: "reverify",
    label: "Closed-Loop Re-Verification",
    description: "Re-run the matcher on demand and auto-close cases that have since cleared.",
    accent: "good",
    Panel: ReverificationPanel,
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
  const [visited, setVisited] = useState<ReadonlySet<ToolId>>(() => new Set());

  const handleSelect = (id: ToolId) => {
    setActive((prev) => (prev === id ? null : id));
    setVisited((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  };

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
                onClick={() => handleSelect(t.id)}
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

        <div className="min-w-0 max-h-[640px] overflow-y-auto rounded-2xl border border-border bg-surface shadow-sm">
          {active === null && (
            <div className="flex min-h-[220px] flex-col items-center justify-center gap-1.5 px-6 py-10 text-center">
              <p className="text-[0.92rem] font-semibold text-ink">Pick a tool on the left</p>
              <p className="max-w-sm text-[0.8rem] text-ink-mute">
                Seven deterministic and AI-assisted views sit behind this queue &mdash; Q&amp;A,
                bank reconciliation, root-cause clustering, matcher auto-resolves, correction
                memory, the hash-chained audit trail, and on-demand re-verification.
              </p>
            </div>
          )}
          {TOOLS.filter((t) => visited.has(t.id)).map((t) => (
            <div key={t.id} className={t.id === active ? "" : "hidden"}>
              <t.Panel />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
