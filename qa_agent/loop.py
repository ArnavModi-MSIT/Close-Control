"""The Q&A agent's tool-calling loop -- structurally the same hand-rolled
ReAct pattern as investigator/loop.py (no agent framework), reusing
investigator's own OllamaToolClient directly rather than duplicating the
HTTP/retry logic a second time. The genuine difference from investigator/
is the question being answered: not "what should happen to this one
escalated case" but "answer this free-text question, grounded in real
tool results, and flag anything you said that doesn't trace back to one."
"""

import json
import time

from investigator.loop import json_safe, _tool_call_args  # same NaN-sanitizer, same tool-args parser
from investigator.ollama_client import OllamaToolClient
from investigator.schema import ToolCallRecord

from . import config
from .grounding import check_grounding
from .schema import QAResult
from .tool_schema import TOOL_SCHEMAS
from .tools import TOOLS, ToolContext

GENERAL_INSTRUCTIONS = """You are the Settlement Q&A Agent for a payment-reconciliation system. A human \
operator is asking a question about the reconciliation data -- overall stats, cash position, root \
causes, or a specific transaction/settlement.

You have tools to look up real data: get_portfolio_summary and get_cash_position_summary for headline \
numbers, search_cases to find escalated cases by filter, get_root_cause_summary for what's actually \
driving the backlog, and per-transaction tools (get_transaction_details, get_settlement_details, \
calculate_settlement_variance, lookup_related_transactions, search_bank_statement, \
get_loan_recovery_schedule, compute_delta) for a specific case once you know its transaction_id.

RULES:
1. Call tools to get real numbers before answering. Never state a rupee amount, count, or percentage \
you did not get from a tool result -- if you do not know, say so and name which tool would answer it, \
do not guess.
2. Never do arithmetic yourself. Always call compute_delta for subtraction; a percentage or total a \
tool already computed and returned to you (e.g. automation_rate_pct) is fine to quote as-is.
3. If a question needs a specific transaction_id you do not have, use search_cases or \
get_root_cause_summary first to find candidates, then look up the specific one.
4. Cite every tool call you used in your final answer as TOOL-1, TOOL-2, etc., in the order you called \
them.
5. If the question cannot be answered from the data available here, say so plainly rather than \
inventing an answer.
6. You are answering a question, not authorizing an action -- nothing you say changes any case's \
status, matches a transaction, or approves anything. Same boundary as everywhere else in this project.
"""

FINAL_SCHEMA_INSTRUCTION = """
You have finished gathering information. Respond with ONLY a JSON object (no markdown fences, no other
text, no further tool calls) with exactly these fields:
{
  "answer": "<your full natural-language answer, citing specific numbers from your tool calls>",
  "citations": ["TOOL-1", "TOOL-2", ...]
}
"""


def ask(question: str, ctx: ToolContext, client: OllamaToolClient | None = None) -> QAResult:
    client = client or OllamaToolClient(model=config.QA_MODEL, host=config.OLLAMA_HOST, think=config.THINK_MODE)
    t0 = time.perf_counter()

    messages = [
        {"role": "system", "content": GENERAL_INSTRUCTIONS},
        {"role": "user", "content": question},
    ]

    tool_log: list[ToolCallRecord] = []
    stopped_reason = "model_finished"
    rounds_used = 0

    for step in range(1, config.MAX_TOOL_ROUNDS + 1):
        rounds_used = step
        try:
            assistant_msg = client.chat_with_tools(messages, TOOL_SCHEMAS)
        except Exception as e:
            # A slow/failed round must not crash the whole answer -- fall
            # through to final_answer() with whatever was gathered so far,
            # same defensive pattern as investigator/loop.py.
            stopped_reason = f"tool_round_failed: {type(e).__name__}: {e}"
            break
        messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []
        if not tool_calls:
            break  # model has enough to answer

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name")
            args = _tool_call_args(fn.get("arguments"))

            if name not in TOOLS:
                result = {"error": f"unknown tool '{name}'"}
            else:
                try:
                    result = TOOLS[name](ctx, **args)
                except Exception as e:
                    # Widened to except Exception (not just TypeError) for
                    # the same reason investigator/loop.py's tool-call site
                    # was: a real data-shape error must degrade to a
                    # recorded error result, never crash the whole answer.
                    result = {"error": f"{name} raised {type(e).__name__}: {e}"}

            tool_log.append(ToolCallRecord(step=step, tool_name=name or "?", arguments=args, result=result))
            messages.append({"role": "tool", "content": json.dumps(json_safe(result), default=str)})
    else:
        stopped_reason = "max_rounds_exceeded"

    try:
        raw = client.final_answer(messages, schema_instruction=FINAL_SCHEMA_INSTRUCTION)
        answer = raw.get("answer") or "[No answer text returned.]"
        citations = raw.get("citations") or []
    except Exception as e:
        answer = f"[Q&A AGENT FAILED TO PRODUCE AN ANSWER: {e}] Try rephrasing the question or check whether Ollama is running."
        citations = []

    grounding = check_grounding(answer, tool_log)
    if not grounding.all_grounded:
        # Surfaced, never hidden -- same "flag, don't silently drop"
        # discipline as agent/evidence.py's unknown_evidence_citations. A
        # human reading the answer sees exactly which figures could not be
        # traced back to a real tool result.
        flagged = ", ".join(f"{n:g}" for n in grounding.ungrounded_numbers)
        answer += (f"\n\n[GROUNDING WARNING: this answer contains number(s) not traced to any tool "
                   f"result in this conversation: {flagged}. Treat with caution.]")

    return QAResult(
        question=question,
        answer=answer,
        citations=citations,
        grounding=grounding,
        tool_log=tool_log,
        tool_rounds_used=rounds_used,
        stopped_reason=stopped_reason,
        elapsed_seconds=round(time.perf_counter() - t0, 3),
        model=client.model,
    )
