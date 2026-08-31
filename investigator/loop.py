"""The agentic investigation loop.

Plain hand-rolled ReAct-style loop -- no agent framework. The model gets
tools, decides what to check and in what order, up to MAX_TOOL_ROUNDS,
then is forced into a final structured verdict. Every tool call and its
real result is recorded in investigation_log, so a human reviewer (or
agent/gate.py) never has to trust "the agent investigated this" as an
unverifiable claim -- the actual trace is right there.

This produces an InvestigationResult, which is schema-compatible with
agent/schema.py's ExceptionResolution -- it flows into the EXACT SAME
agent/gate.py, unmodified. The investigation gets deeper; the authority
boundary (matcher's type is authoritative, gate decides auto-resolve vs
escalate, confidence alone never authorizes anything) does not move.
"""

import json
import math
import time

from . import config
from .ollama_client import OllamaToolClient
from .schema import InvestigationResult, ToolCallRecord
from .tool_schema import TOOL_SCHEMAS
from .tools import TOOLS, ToolContext

GENERAL_INSTRUCTIONS = """You are the Exception Investigation Agent in a payment settlement \
reconciliation system. A deterministic matching engine already did all the arithmetic and \
flagged this transaction as an exception it could not auto-resolve.

You have tools available to actively investigate this case, the way a human reconciliation \
analyst would: get_transaction_details and get_settlement_details for authoritative facts \
beyond the initial evidence block, calculate_settlement_variance for the full financial \
breakdown, search_bank_statement to look for a candidate bank posting, \
lookup_related_transactions to check for a wider pattern, and compute_delta for any other \
arithmetic -- rather than just classifying from a static evidence block.

RULES:
1. Use tools to investigate before concluding. Don't guess what a related transaction, \
settlement, or bank posting looks like -- look it up.
2. Never do arithmetic yourself. Always call compute_delta or calculate_settlement_variance.
3. Call tools only when they would actually change your conclusion. Investigating for its own \
sake wastes rounds you have a limited number of.
4. When you have enough to conclude (or you're confident no tool will help further), stop \
calling tools and give your final answer.
5. Cite specific values from your evidence AND from tool results in your root_cause -- never \
invent a number. In evidence_used, cite tool results as TOOL-1, TOOL-2, etc. in the order you \
called them (the first tool call you make is TOOL-1, the second is TOOL-2, and so on) \
alongside any EVIDENCE-N fields you used from the block below -- e.g. evidence_used: \
["EVIDENCE-4", "TOOL-1"]. Only cite a TOOL-N id if you actually called that tool and used its \
result -- never cite one you didn't call.
6. search_bank_statement's candidates include a candidate_status field -- only cite candidates \
marked 'unclaimed'; a candidate marked 'already_matched_elsewhere' belongs to a DIFFERENT \
settlement and is not valid evidence for this case, even if the amount/date happen to match.
7. You are proposing a resolution, not authorizing one. A separate deterministic gate decides \
whether your proposal is auto-applied or sent to a human -- exactly as before, tools don't \
change that.

RELEVANT POLICY FOR THIS CASE:
"""


def _tool_call_args(raw_args) -> dict:
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    return raw_args or {}


def json_safe(obj):
    """Recursively replaces a NaN/Infinity float with None. A tool result
    that passes through pandas/numpy data routinely carries one for a
    genuinely missing value -- e.g. get_settlement_details()'s matched_utrs
    is None (real) for a bank posting with no UTR, but pandas represents
    that as a raw float NaN, not None, when it's pulled out of a Series.
    Python's own json.dumps() happily writes that as a non-standard literal
    NaN token by default, which is NOT valid JSON -- found live via a real
    500 on GET /api/cases/trn-000070: Starlette's JSONResponse sets
    allow_nan=False and correctly refuses to serialize it, several layers
    downstream of where the NaN was first produced, by which point it was
    already written into investigation_log.jsonl and persisted into
    Postgres. Applied here, at the JSONL append boundary in
    run_investigator.py/run_demo.py, rather than only at the one tool call
    site the bug was first found in -- this protects the log against ANY
    tool (or future tool) leaking a stray NaN, not just this one field.
    Already-written entries from before this existed still need a
    one-time repair -- see backfill_json_sanitization.py."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def tool_evidence_ids(result: InvestigationResult) -> frozenset:
    """TOOL-1, TOOL-2, ... one per real investigation_log entry, in call
    order -- matches GENERAL_INSTRUCTIONS' citation convention exactly.
    Pass this to agent.gate.apply_gate()'s extra_valid_evidence_ids so a
    citation like 'TOOL-2' validates correctly instead of always showing as
    unknown (agent/evidence.py's KNOWN_EVIDENCE_FIELDS only covers the
    static EVIDENCE-N fields from the initial block, not per-investigation
    tool results).

    ALSO includes the literal tool_name of every tool genuinely called in
    THIS investigation (e.g. 'search_bank_statement'), as an accepted
    alias alongside its 'TOOL-N' label -- found by checking real data, not
    assumed: across investigation_log.jsonl, 148 of 313 investigations
    with real evidence (47%) cite a tool by its own name instead of the
    instructed TOOL-N convention, e.g. evidence_used=['EVIDENCE-4',
    'search_bank_statement']. That's the model's dominant citation style
    for tool results, not a rare slip, and every one of those 148 cases was
    showing a real, valid tool call as "(not a known evidence field)" to a
    human reviewer -- the same false-positive class this project already
    fixed twice for other citation formats (EVIDENCE-N field names, TOOL-N
    labels themselves).

    Deliberately still NOT permissive: only a tool name that ACTUALLY
    appears in result.investigation_log is accepted. A citation naming a
    tool that was never called in this investigation still correctly fails
    validation -- this widens the accepted FORMAT, not what counts as
    genuine evidence."""
    numbered = {f"TOOL-{i + 1}" for i in range(len(result.investigation_log))}
    by_name = {entry.tool_name for entry in result.investigation_log}
    return frozenset(numbered | by_name)


def investigate(report_row: dict, policy_block: str, evidence_block: str,
                 ctx: ToolContext, client: OllamaToolClient | None = None,
                 data_dir: str | None = None) -> InvestigationResult:
    client = client or OllamaToolClient()
    t0 = time.perf_counter()

    # See corrections.py's docstring -- a past human override of a similar
    # case (same matcher exception_type) is surfaced as a few-shot example,
    # same principle and same call as agent/client.py's single-shot path.
    # "" (no correction on record for this type) when there's nothing to add.
    from corrections import correction_block_for, DEFAULT_DATA_DIR
    exc_type = report_row.get("final_exception_type") or "unknown"
    correction_block = correction_block_for(exc_type, data_dir or DEFAULT_DATA_DIR)

    system_prompt = GENERAL_INSTRUCTIONS + policy_block + correction_block
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Investigate and resolve this exception.\n\n{evidence_block}"},
    ]

    investigation_log: list[ToolCallRecord] = []
    stopped_reason = "model_finished"
    rounds_used = 0

    for step in range(1, config.MAX_TOOL_ROUNDS + 1):
        rounds_used = step
        try:
            assistant_msg = client.chat_with_tools(messages, TOOL_SCHEMAS)
        except Exception as e:
            # A slow/failed round mid-investigation must not crash the whole
            # run -- fall through to final_answer() with whatever context
            # was gathered so far, same as agent/providers/ollama.py's
            # defensive pattern for the single-shot agent.
            stopped_reason = f"tool_round_failed: {type(e).__name__}: {e}"
            break
        messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []
        if not tool_calls:
            break  # model has nothing more to check

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
                    # Was `except TypeError` only (wrong/missing arguments) --
                    # a KeyError from a settlement row missing an expected
                    # field, an IndexError from an empty slice, a ValueError
                    # from an unexpected date reaching pd.to_datetime, etc.
                    # all propagated straight out of investigate() uncaught,
                    # crashing the entire batch run (run_investigator.py's
                    # sequential loop has no per-case guard). Widened to match
                    # the tool_round_failed pattern two lines up: a failing
                    # tool call degrades to a recorded error result instead of
                    # crashing, same as a failing LLM round already does.
                    # Found via external review.
                    result = {"error": f"{name} raised {type(e).__name__}: {e}"}

            investigation_log.append(ToolCallRecord(step=step, tool_name=name or "?",
                                                      arguments=args, result=result))
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})
    else:
        stopped_reason = "max_rounds_exceeded"

    try:
        raw = client.final_answer(messages)
    except Exception as e:
        raw = {
            "exception_type": report_row.get("final_exception_type") or "unknown",
            "policy_id": "NONE",
            "root_cause": f"[INVESTIGATION FAILED TO PRODUCE A FINAL ANSWER: {e}] "
                           f"Escalating without a completed verdict.",
            "evidence_used": [], "recommended_action": "Escalate for manual review -- agent call failed.",
            "confidence": 0.0, "sufficient_evidence": False,
            "investigation_summary": f"{rounds_used} tool round(s) completed before failure.",
        }

    raw["investigation_log"] = [r.model_dump() for r in investigation_log]
    raw["tool_rounds_used"] = rounds_used
    raw["stopped_reason"] = stopped_reason
    raw["elapsed_seconds"] = round(time.perf_counter() - t0, 3)

    # A tool round failing mid-investigation means the investigation was NOT
    # actually complete -- but until now, nothing stopped the model's own
    # final_answer() call (a SEPARATE call that can succeed even when an
    # earlier round didn't) from claiming sufficient_evidence=True anyway.
    # stopped_reason recorded the failure but never constrained the verdict
    # that reached agent/gate.py -- found via external review. Deterministic
    # override, not a trust-the-model fix: the model's own claim is
    # overwritten, not merely flagged, since gate.py's sufficient_evidence
    # condition must mean what it says.
    if stopped_reason.startswith("tool_round_failed") and raw.get("sufficient_evidence"):
        raw["sufficient_evidence"] = False
        raw["confidence"] = min(raw.get("confidence", 0.0), 0.3)
        raw["root_cause"] = (raw.get("root_cause") or "") + (
            f" [OVERRIDDEN: a tool round failed mid-investigation ({stopped_reason}) -- "
            f"sufficient_evidence forced False regardless of the model's own claim, since "
            f"the investigation this verdict is based on was not actually complete.]"
        )

    return InvestigationResult(**raw)
