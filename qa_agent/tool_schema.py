"""JSON-schema tool declarations sent to the model, in Ollama's
OpenAI-compatible `tools` format. The 7 per-transaction schemas are
investigator/tool_schema.py's, unmodified -- reused rather than
re-declared so the two modules can never describe the same tool two
different ways. The 4 portfolio-level schemas below are new."""

from investigator.tool_schema import TOOL_SCHEMAS as _INVESTIGATOR_TOOL_SCHEMAS

_QA_ONLY_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": (
                "Headline counts across the WHOLE dataset: how many transactions are "
                "clean, how many the deterministic matcher auto-resolved on its own, "
                "how many are escalated for human/AI review, the escalated population's "
                "total amount at risk, and its breakdown by exception type. Use this for "
                "any question about overall volume, automation rate, or 'how many X' "
                "across the whole portfolio -- not for a single transaction."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cases",
            "description": (
                "Find escalated cases matching filters: exception_type, an amount range, "
                "or a specific merchant_id. This ALWAYS searches escalated cases only -- "
                "do not pass 'escalated' itself as exception_type, it is not a real "
                "exception type. Returns up to a capped number of matches (sorted by "
                "amount at risk, highest first) plus the TRUE total_matches count and a "
                "truncated flag -- always report the true total if the caller asked 'how "
                "many', never just the length of the returned sample. Use this to answer "
                "questions like 'show me the biggest missing_bank_reference cases' or "
                "'which cases from merchant X are still open'. To find the escalated count "
                "with no filter, call this with no arguments, or use get_portfolio_summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exception_type": {"type": "string", "description": "Optional. A real exception type "
                                        "like 'missing_bank_reference', 'partial_refund', "
                                        "'unexplained_shortage', 'chargeback_received' -- never 'escalated' "
                                        "or 'pending', those are statuses, not exception types."},
                    "min_amount_rupees": {"type": "number", "description": "Optional lower bound on amount at risk."},
                    "max_amount_rupees": {"type": "number", "description": "Optional upper bound on amount at risk."},
                    "merchant_id": {"type": "string", "description": "Optional. e.g. 'merch_001'."},
                    "limit": {"type": "integer", "description": "Optional. Max cases to return (default 20)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_root_cause_summary",
            "description": (
                "Deterministic root-cause clustering of the ENTIRE escalated queue -- how "
                "many underlying problems (not tickets) are actually driving the backlog, "
                "the amplification factor, and the top clusters by case count. Use this "
                "for questions like 'what's actually driving the review queue' or 'what's "
                "the single biggest problem right now' -- it answers at the level of "
                "underlying causes, not individual transactions."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_position_summary",
            "description": (
                "The cash position snapshot: confirmed (bank-verified) rupees, in-transit "
                "(forecasted, not yet due) rupees, held/at-risk rupees, and the projected "
                "cash position (confirmed + in-transit only -- at-risk money is "
                "deliberately excluded, never folded into a guess). Use this for any "
                "question about how much money is confirmed, in transit, at risk, or "
                "projected."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_SCHEMAS = list(_INVESTIGATOR_TOOL_SCHEMAS) + _QA_ONLY_SCHEMAS
