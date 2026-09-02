"""JSON-schema tool declarations sent to the model, in Ollama's
OpenAI-compatible `tools` format. Kept separate from tools.py's actual
implementations -- this file describes the interface the model sees,
tools.py is what actually runs."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_transaction_details",
            "description": (
                "Get authoritative fields for the case's own transaction_id -- payment "
                "method, gross amount, expected fee/tax, gateway status, signature_valid, "
                "and (when relevant) refund_id/refund_reason straight from the gateway "
                "record. Use this to re-check the case's own authoritative detail instead "
                "of relying only on the initial evidence block, especially for "
                "signature_verification_failed (check signature_valid) or partial_refund "
                "(check refund_id/refund_reason) cases."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "The case's own transaction_id."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_settlement_details",
            "description": (
                "Get settlement-level match facts: which bank posting(s) it matched to, "
                "the matcher's own match confidence, every member transaction, and the "
                "amount delta. Use this for N:1 (many payments, one settlement) or 1:N "
                "(one settlement, split across bank postings) cases, where the settlement "
                "itself -- not any single member payment -- is the unit that actually "
                "matters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {"type": "string", "description": "The case's own settlement_id (from the evidence block)."},
                },
                "required": ["settlement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_settlement_variance",
            "description": (
                "One deterministic call for the full financial breakdown of this case: "
                "gross amount, expected fee/tax, expected vs. observed net, and the net "
                "delta -- all in rupees, all real fields, nothing invented. Prefer this "
                "over compute_delta when you need the whole picture, not just one "
                "subtraction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "The case's own transaction_id."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_related_transactions",
            "description": (
                "Find other transactions from the SAME merchant as the case under "
                "investigation, captured within a few days of it. Use this to check "
                "whether a flagged problem is isolated to this one transaction or "
                "part of a wider pattern (e.g. the whole settlement batch failed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "The case's own transaction_id."},
                    "days": {"type": "integer", "description": "Window size in days either side. Default 2."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_bank_statement",
            "description": (
                "Search the bank statement for a candidate posting near this transaction's "
                "own captured date and expected amount. Use this for missing-bank-reference "
                "cases to actively look for an unclaimed posting instead of assuming none "
                "exists. The date/amount window is derived automatically from the "
                "transaction itself -- you only provide the transaction_id. Each returned "
                "candidate has a candidate_status: 'unclaimed' (genuinely available as "
                "evidence) or 'already_matched_elsewhere' (already consumed by a different "
                "settlement's match -- a date/amount coincidence, NOT valid evidence for this "
                "case). Only cite unclaimed candidates as evidence; unclaimed_candidate_count "
                "tells you how many there actually are before you look at the list. If NOTHING "
                "is found in the strict window, the response may instead carry a `near_miss` "
                "field -- the single closest candidate found in a much wider search, with "
                "amount_diff_rupees/date_diff_days/explanation saying exactly why it fell "
                "short. This is NOT a valid match -- never cite it as evidence a posting was "
                "found -- but it is useful context for root_cause (e.g. \"no exact match, but "
                "the closest candidate was Rs.340 short\") and for recommending what a human "
                "should chase next."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "The case's own transaction_id."},
                    "window_days": {"type": "integer", "description": "Days either side of the captured date to search. Default 5."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loan_recovery_schedule",
            "description": (
                "Check Razorpay Capital's recovery ledger for this transaction. A settlement "
                "can credit LESS than expected because a contracted working-capital advance "
                "deducted its agreed percentage -- money collected, not money missing. Use "
                "this whenever a shortfall or a negative adjustment needs explaining, before "
                "concluding it is an unexplained shortage or a refund. Read three fields "
                "carefully and do not conflate them: merchant_has_active_advance (this "
                "merchant borrows at all), recovery_found_for_this_transaction (a recovery "
                "was booked against THIS settlement -- an advance alone explains nothing), "
                "and reconciles_delta (the recovery accounts for the shortfall IN FULL). "
                "Only when reconciles_delta is true is the shortfall explained; if it is "
                "false, residual_after_recovery_rupees is genuinely unexplained and the case "
                "must escalate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "The case's own transaction_id."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_delta",
            "description": (
                "Subtract b from a. This is the ONLY way to do arithmetic during this "
                "investigation -- never compute a difference yourself, always call this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    },
]
