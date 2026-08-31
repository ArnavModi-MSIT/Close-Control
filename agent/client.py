"""Dispatches to whichever provider is configured (mock / groq / anthropic).
Runs the evidence-completeness check FIRST -- if required evidence is
missing, short-circuits to an honest 'insufficient_evidence' result
without ever calling the LLM at all.

The system prompt sent per call includes ONLY the policy relevant to
that specific exception_type, not the full KB -- this is both correct
grounded-generation practice (don't hand the model context it doesn't
need for this decision) and keeps token usage per call small enough to
fit comfortably within free-tier rate limits (Groq's free tier is
8000 tokens/minute; the old full-KB prompt alone was ~1500-2500 tokens
per call, leaving room for only 3-4 calls/minute).
"""

from . import config
from .schema import ExceptionResolution
from .policy_kb import get_policy
from .evidence import build_evidence, build_policy_block
from .evidence_check import check_evidence_complete

GENERAL_INSTRUCTIONS = """You are the Exception Resolution Agent in a payment settlement \
reconciliation system. A deterministic matching engine has already done all the \
arithmetic and flagged this transaction as an exception it could not auto-resolve.

Your job:
1. Confirm or reclassify the exception type based on the evidence provided.
2. Cite the specific policy rule (POLICY-###) your resolution is grounded in.
3. Explain the root cause using ONLY the specific evidence values given -- cite them.
4. Recommend the action specified by that policy.
5. State your confidence (0.0-1.0).
6. If the evidence is genuinely insufficient or contradictory, or the policy doesn't \
clearly cover this case, set sufficient_evidence=False and explain why -- do NOT guess.

CRITICAL RULES:
- Never invent a number. Every amount you cite must come from the evidence block.
- Never propose a resolution without citing a policy_id.
- If uncertain, prefer a lower confidence score over a confident-sounding guess.
- You are proposing a resolution, not authorizing one. A separate deterministic \
gate decides whether your proposal is auto-applied or sent to a human.

RELEVANT POLICY FOR THIS CASE:
"""


def _get_provider():
    if config.LLM_PROVIDER == "mock":
        from .providers.mock import MockProvider
        return MockProvider()
    elif config.LLM_PROVIDER == "groq":
        if not config.GROQ_API_KEY:
            raise RuntimeError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set. Add it to your .env, "
                "or set LLM_PROVIDER=mock to run with $0 cost."
            )
        from .providers.groq import GroqProvider
        return GroqProvider(api_key=config.GROQ_API_KEY, model=config.GROQ_MODEL)
    elif config.LLM_PROVIDER == "ollama":
        from .providers.ollama import OllamaProvider
        return OllamaProvider(model=config.OLLAMA_MODEL, host=config.OLLAMA_HOST)
    elif config.LLM_PROVIDER == "anthropic":
        if not config.API_KEY:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
            )
        from .providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=config.API_KEY, model=config.ANTHROPIC_MODEL,
                                  max_tokens=config.MAX_TOKENS)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={config.LLM_PROVIDER!r}. "
                          f"Use 'mock', 'groq', 'ollama', or 'anthropic'.")


_provider = None


def get_active_provider():
    global _provider
    if _provider is None:
        _provider = _get_provider()
    return _provider


def resolve_exception(report_row: dict, use_policy_retrieval: bool = True,
                       data_dir: str | None = None) -> ExceptionResolution:
    """Main entry point. report_row: one dict from matching.report's output.

    use_policy_retrieval=False strips the retrieved policy block from the
    prompt, leaving only the general instructions + evidence. Used only by
    the RAG ablation study (run_rag_ablation.py) to measure what retrieval
    actually buys the agent -- the normal pipeline (run_agent.py) never
    disables it.

    data_dir: where to look for corrections.py's correction_log.jsonl
    (see that module's docstring) -- defaults to corrections.DEFAULT_DATA_DIR,
    the main demo's data/. Every existing caller keeps working unchanged;
    this is additive."""
    is_complete, missing = check_evidence_complete(report_row)
    if not is_complete:
        exc_type = report_row.get("final_exception_type") or "unknown"
        try:
            policy = get_policy(exc_type)
            policy_id = policy["policy_id"]
        except KeyError:
            policy_id = "NONE"
        return ExceptionResolution(
            exception_type=exc_type,
            policy_id=policy_id,
            root_cause=f"[EVIDENCE INCOMPLETE, no LLM call made] Missing required "
                       f"fields for {exc_type}: {missing}",
            evidence_used=[],
            recommended_action="Escalate -- required evidence not available.",
            confidence=0.0,
            sufficient_evidence=False,
        )

    provider = get_active_provider()
    exc_type = report_row.get("final_exception_type") or "unknown"
    if use_policy_retrieval:
        try:
            policy = get_policy(exc_type)
            policy_block = build_policy_block(policy, policy["policy_id"])
        except KeyError:
            policy_block = "[NO MATCHING POLICY FOUND for this exception_type -- treat as insufficient evidence.]"
    else:
        policy_block = (
            "[NO POLICY REFERENCE PROVIDED. Determine the correct POLICY-### id, typical "
            "cause, and resolution action from your own general knowledge of payment "
            "reconciliation practice. If you cannot determine this with confidence, set "
            "sufficient_evidence=False rather than guessing.]"
        )

    from corrections import correction_block_for, DEFAULT_DATA_DIR
    correction_block = correction_block_for(exc_type, data_dir or DEFAULT_DATA_DIR)

    system_prompt = GENERAL_INSTRUCTIONS + policy_block + correction_block
    evidence_block = build_evidence(report_row)
    user_message = f"Resolve this exception.\n\n{evidence_block}"
    return provider.resolve(system_prompt, user_message)
