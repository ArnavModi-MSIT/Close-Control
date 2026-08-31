import { useState } from "react";
import { useAskQA } from "../hooks/useQueries";
import type { QAResult, ToolCallRecord } from "../types";

const EXAMPLE_QUESTIONS = [
  "How much cash is confirmed right now, and what's in transit?",
  "What's the single biggest thing driving the review queue backlog?",
  "Show me the largest missing_bank_reference cases.",
];

function ToolTraceItem({ step }: { step: ToolCallRecord }) {
  const [expanded, setExpanded] = useState(false);
  const resultStr = JSON.stringify(step.result, null, 2);
  const isLong = resultStr.length > 200;
  const shown = expanded || !isLong ? resultStr : resultStr.slice(0, 200) + "…";

  return (
    <li className="rounded-lg border border-border bg-surface-2 p-2.5 text-[0.78rem] leading-relaxed"
        style={{ overflowWrap: "anywhere" }}>
      <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[0.72rem]">
        <span className="rounded bg-accent px-1.5 py-0.5 font-semibold text-white">{step.step}</span>
        <span className="font-semibold text-accent">{step.tool_name}</span>
      </div>
      {Object.keys(step.arguments).length > 0 && (
        <div className="mb-1.5">
          <div className="mb-0.5 text-[0.68rem] tracking-wide text-ink-mute uppercase">Arguments</div>
          <div className="rounded bg-surface px-2 py-1.5 font-mono text-[0.75rem]">
            {Object.entries(step.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")}
          </div>
        </div>
      )}
      <div>
        <div className="mb-0.5 text-[0.68rem] tracking-wide text-ink-mute uppercase">Result</div>
        <pre className="whitespace-pre-wrap rounded bg-surface px-2 py-1.5 font-mono text-[0.75rem]">{shown}</pre>
        {isLong && (
          <button type="button" onClick={() => setExpanded((v) => !v)}
                  className="mt-1 text-[0.72rem] font-semibold text-accent">
            {expanded ? "Show less" : "Show full result"}
          </button>
        )}
      </div>
    </li>
  );
}

function AnswerCard({ result }: { result: QAResult }) {
  const grounded = result.grounding.all_grounded;
  return (
    <div className={`mt-3 rounded-xl border-[1.5px] p-3.5 ${grounded ? "border-good/40 bg-good-soft" : "border-warn/40 bg-warn-soft"}`}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[0.72rem] tracking-wide text-ink-soft uppercase">Answer</span>
        <span
          className={`rounded-full px-2 py-0.5 text-[0.68rem] font-semibold ${grounded ? "bg-good text-white" : "bg-warn text-ink"}`}
          title={
            grounded
              ? "Every number in this answer was traced back to a real tool result -- none of it was invented."
              : "At least one number in this answer could not be traced to any real tool result. Treat with caution."
          }
        >
          {grounded ? "GROUNDED" : "UNGROUNDED NUMBER(S)"}
        </span>
      </div>
      <p className="text-[0.9rem] whitespace-pre-wrap text-ink">{result.answer}</p>
      {result.citations.length > 0 && (
        <p className="mt-2 text-[0.74rem] text-ink-mute">
          Citations: <span className="font-mono">{result.citations.join(", ")}</span>
        </p>
      )}
      <p className="mt-1 text-[0.72rem] text-ink-mute">
        {result.tool_rounds_used} tool round(s) · {result.elapsed_seconds}s · {result.model ?? "unknown model"}
      </p>
      {result.tool_log.length > 0 && (
        <details className="mt-2.5">
          <summary className="cursor-pointer text-[0.76rem] font-semibold text-accent">
            Tool trace ({result.tool_log.length} call{result.tool_log.length === 1 ? "" : "s"})
          </summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {result.tool_log.map((step, i) => (
              <ToolTraceItem key={`${step.step}-${step.tool_name}-${i}`} step={step} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function QAPanel() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const { mutate, data, isPending, isError, error, reset } = useAskQA();

  const ask = (q: string) => {
    if (!q.trim()) return;
    setQuestion(q);
    mutate(q);
  };

  return (
    <div className="rounded-2xl border border-border border-l-4 border-l-accent bg-surface shadow-sm">
      <button type="button" onClick={() => setOpen((v) => !v)}
              className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
              aria-expanded={open}>
        <div>
          <h2 className="text-[1.05rem] font-bold text-ink">Settlement Q&amp;A</h2>
          <p className="mt-0.5 text-[0.82rem] text-ink-soft">
            Ask a question about the reconciliation data — grounded in real tool calls, not guessed.
          </p>
        </div>
        <span className={`flex-shrink-0 rounded-full border border-border-2 px-3 py-1.5 font-mono text-[0.76rem] text-ink-soft transition-transform ${open ? "rotate-180" : ""}`}>
          &#9660;
        </span>
      </button>

      {open && (
        <div className="border-t border-border px-6 py-5">
          <form
            onSubmit={(e) => { e.preventDefault(); ask(question); }}
            className="flex flex-wrap gap-2"
          >
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. What's driving the review queue backlog?"
              className="min-w-0 flex-1 rounded-lg border border-border-2 bg-surface px-3 py-2 text-[0.88rem] text-ink"
            />
            <button
              type="submit"
              disabled={isPending || !question.trim()}
              className="rounded-lg bg-accent px-4 py-2 text-[0.86rem] font-semibold text-white disabled:opacity-50"
            >
              {isPending ? "Thinking…" : "Ask"}
            </button>
          </form>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLE_QUESTIONS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                disabled={isPending}
                className="rounded-full border border-border-2 bg-surface-2 px-2.5 py-1 text-[0.72rem] text-ink-soft disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>

          {isPending && (
            <p className="mt-3 text-[0.84rem] text-ink-mute">
              Calling real tools and reasoning over the result — this runs on local Ollama and
              typically takes 30–120 seconds, not a few seconds. Hang tight.
            </p>
          )}

          {isError && (
            <div className="mt-3 rounded-xl border-[1.5px] border-crit bg-surface px-3.5 py-2.5">
              <p className="text-[0.86rem] text-crit">
                {error instanceof Error ? error.message : "The Q&A agent is unavailable."}
              </p>
              <button type="button" onClick={() => reset()}
                      className="mt-1.5 text-[0.8rem] font-semibold text-accent">
                Dismiss
              </button>
            </div>
          )}

          {data && <AnswerCard result={data} />}
        </div>
      )}
    </div>
  );
}
