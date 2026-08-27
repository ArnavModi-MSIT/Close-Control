import { useRef, useState } from "react";
import type { InvestigationDetail } from "../../types";

function ToolTraceItem({ step }: { step: InvestigationDetail["log"][number] }) {
  const [expanded, setExpanded] = useState(false);
  const resultStr = JSON.stringify(step.result, null, 2);
  const isLong = resultStr.length > 200;
  const shown = expanded || !isLong ? resultStr : resultStr.slice(0, 200) + "…";

  return (
    <li
      className="rounded-lg border border-border bg-surface-2 p-2.5 text-[0.78rem] leading-relaxed"
      style={{ overflowWrap: "anywhere" }}
    >
      <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[0.72rem]">
        <span className="rounded bg-accent px-1.5 py-0.5 font-semibold text-white">
          {step.step}
        </span>
        <span className="font-semibold text-accent">{step.tool_name}</span>
      </div>
      {Object.keys(step.arguments).length > 0 && (
        <div className="mb-1.5">
          <div className="mb-0.5 text-[0.68rem] tracking-wide text-ink-mute uppercase">Arguments</div>
          <div className="rounded bg-surface px-2 py-1.5 font-mono text-[0.75rem]">
            {Object.entries(step.arguments)
              .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
              .join(", ")}
          </div>
        </div>
      )}
      <div>
        <div className="mb-0.5 text-[0.68rem] tracking-wide text-ink-mute uppercase">Result</div>
        <pre className="whitespace-pre-wrap rounded bg-surface px-2 py-1.5 font-mono text-[0.75rem]">
          {shown}
        </pre>
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-1 text-[0.72rem] font-semibold text-accent"
          >
            {expanded ? "Show less" : "Show full result"}
          </button>
        )}
      </div>
    </li>
  );
}

function CopyDraftButton({ text }: { text: string }) {
  const [label, setLabel] = useState("Copy");
  const textRef = useRef<HTMLPreElement>(null);

  const fallbackSelect = () => {
    const el = textRef.current;
    if (!el) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    setLabel("Selected — Ctrl+C");
    setTimeout(() => setLabel("Copy"), 2000);
  };

  const handleClick = () => {
    if (!navigator.clipboard?.writeText) {
      fallbackSelect();
      return;
    }
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setLabel("Copied");
        setTimeout(() => setLabel("Copy"), 1500);
      })
      .catch(fallbackSelect);
  };

  return (
    <div className="mt-3">
      <div className="flex items-center justify-between border-t border-border pt-1.5">
        <span className="flex items-center gap-1.5 text-[0.86rem] text-ink-soft">
          Drafted communication
          <span
            className="rounded-full bg-warn-soft px-1.5 py-0.5 text-[0.64rem] font-semibold tracking-wide text-warn"
            title="This is a proposal for a human to review and send -- the system never sends anything on its own."
          >
            DRAFT — NOT SENT
          </span>
        </span>
        <button
          type="button"
          onClick={handleClick}
          className="rounded-lg border border-border-2 bg-surface px-2.5 py-1 text-[0.76rem]"
        >
          {label}
        </button>
      </div>
      <pre
        ref={textRef}
        className="mt-1.5 rounded-lg border border-border bg-surface-2 p-2.5 font-sans text-[0.82rem] whitespace-pre-wrap"
      >
        {text}
      </pre>
    </div>
  );
}

export function InvestigationSection({
  inv,
  isCasePrimary,
}: {
  inv: InvestigationDetail;
  /** True when this investigation IS the case's primary AI proposal
   * (ai_proposal.resolution_source === "investigator") -- i.e. gate_decision
   * here is what actually happened to the case. When false, the
   * investigation is additive enrichment on top of a DIFFERENT primary
   * proposal (the single-shot agent's): gate_decision is then only what
   * this investigation's OWN evidence would have produced on its own, not
   * necessarily the case's real outcome -- e.g. this investigation could
   * say "auto_resolve" while the case itself is still pending, because the
   * single-shot proposal that's actually primary reached a different
   * verdict. Conflating the two was flagged by an external review as a
   * real, reproducible mislabeling risk, not just a hypothetical one. */
  isCasePrimary: boolean;
}) {
  const outcomeTag =
    inv.gate_decision === "auto_resolve" ? (
      <span
        className="rounded-full bg-accent px-2 py-0.5 text-[0.7rem] font-semibold text-white"
        title={
          isCasePrimary
            ? "The gate actually auto-resolved this case based on this investigation."
            : "This investigation's OWN evidence would clear the gate, but it is enrichment on a different (already-decided) primary proposal -- this is not necessarily what happened to the case. See the case status above."
        }
      >
        {isCasePrimary ? "AUTO-RESOLVED" : "GATE: WOULD AUTO-RESOLVE"}
      </span>
    ) : (
      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[0.7rem] font-semibold text-accent">
        {inv.tool_rounds} tool round(s)
      </span>
    );

  return (
    <div className="mb-5 last:mb-0">
      <h4 className="mb-2.5 flex items-center gap-2 text-[0.75rem] tracking-wide text-ink-soft uppercase">
        Investigation {outcomeTag}
        {!isCasePrimary && (
          <span
            className="rounded-full bg-surface-2 px-2 py-0.5 text-[0.66rem] font-semibold tracking-wide text-ink-mute normal-case"
            title="This investigation ran AFTER the case's primary AI proposal (above) was already frozen -- it's additive enrichment, not a replacement. The primary proposal's own exception type/root cause/confidence are what the gate actually decided on."
          >
            enrichment — not the primary proposal
          </span>
        )}
      </h4>
      <p className="text-[0.86rem] text-ink-soft">{inv.summary}</p>
      {inv.log.length > 0 && (
        <ul className="mt-2.5 flex flex-col gap-1.5">
          {inv.log.map((step) => (
            <ToolTraceItem key={step.step + step.tool_name} step={step} />
          ))}
        </ul>
      )}
      {inv.drafted_communication && <CopyDraftButton text={inv.drafted_communication} />}
    </div>
  );
}
