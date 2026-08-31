import type { GateConditionCheck } from "../../types";

// Structured PASS/FAIL breakdown of the deterministic gate's seven conditions --
// makes "AI recommendation" vs. "system decision" an explicit, row-by-row
// distinction instead of one merged status. Falls back to the free-text
// reasons list (in DetailPanel.tsx) for cases seeded before this field
// existed on agent/gate.py's output -- there is no fabricated version of
// this for legacy entries, it's just unavailable.
export function GateChecklist({ checks }: { checks: GateConditionCheck[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {checks.map((c, i) => (
        <li
          key={i}
          className={`flex items-start gap-2.5 rounded-lg border px-2.5 py-2 text-[0.83rem] ${
            c.passed ? "border-good/30 bg-good-soft" : "border-crit/30 bg-crit-soft"
          }`}
        >
          <span
            className={`mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full font-mono text-[0.62rem] font-bold ${
              c.passed ? "bg-good text-white" : "bg-crit text-white"
            }`}
          >
            {c.passed ? "✓" : "✗"}
          </span>
          <span>
            <span className="font-semibold">{c.name}</span>
            <span className="block text-[0.78rem] text-ink-soft">{c.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
