"""Policy knowledge base. Each rule is what the agent retrieves and must
cite (policy_id) before proposing a resolution. Deliberately a small,
readable lookup table -- not a vector DB -- since the exception taxonomy
is fixed and small; a KB this size needs retrieval-by-key, not semantic
search.

auto_resolvable here means "the policy, in principle, permits an
autonomous system to resolve this type" -- the actual auto-resolve
decision is still gated by confidence + risk ceiling in gate.py. This
field alone never authorizes anything.
"""

POLICY_KB = {
    "timing_lag_beyond_t2": {
        "policy_id": "POLICY-001",
        "description": "Settlement landed later than the standard T+2 business-day window.",
        "typical_cause": "Bank/network processing delay, holiday calendar edge case, or "
                          "high-volume settlement batch congestion.",
        "resolution_action": "No action needed if funds have arrived and amount matches "
                              "exactly. Log the delay for SLA tracking.",
        "auto_resolvable": True,
        "risk_class": "low",
    },
    "fee_variance": {
        "policy_id": "POLICY-002",
        "description": "Gateway-charged fee differs from the payment method's published MDR.",
        "typical_cause": "Promotional MDR rate, tier change not yet reflected in ledger, "
                          "or a gateway-side fee calculation error.",
        "resolution_action": "Verify against current MDR schedule; if genuinely a gateway "
                              "error, file a fee dispute. If a known promo/tier change, "
                              "update the ledger's expected rate.",
        "auto_resolvable": True,
        "risk_class": "low",
    },
    "duplicate_retry": {
        "policy_id": "POLICY-003",
        "description": "Failed payment attempt(s) preceded a successful one for the same order.",
        "typical_cause": "Normal gateway routing/retry behavior (multi-connection failover). "
                          "No financial consequence -- only the successful attempt settles.",
        "resolution_action": "No action needed. This is expected system behavior, not an "
                              "exception.",
        "auto_resolvable": True,
        "risk_class": "low",
    },
    "missing_bank_reference": {
        "policy_id": "POLICY-004",
        "description": "The bank posting for this settlement has no UTR -- the settlement "
                        "cannot be independently verified against the bank record.",
        "typical_cause": "Bank statement data-quality issue, delayed UTR assignment, or "
                          "an incomplete NEFT/RTGS record.",
        "resolution_action": "Escalate to treasury/ops to request the UTR from the bank "
                              "directly. Do not confirm settlement completion without it -- "
                              "the amount cannot be independently verified.",
        "auto_resolvable": False,
        "risk_class": "medium",
    },
    "partial_refund": {
        "policy_id": "POLICY-005",
        "description": "A refund/adjustment reduced the settlement amount below the gross "
                        "payment.",
        "typical_cause": "Customer-initiated refund, order cancellation, or product return, "
                          "recorded against this settlement.",
        "resolution_action": "Verify the refund_id and refund_reason against the order "
                              "management system. If it matches a legitimate refund record, "
                              "confirm; otherwise escalate as a discrepancy.",
        "auto_resolvable": False,
        "risk_class": "medium",
    },
    "deemed_success_ambiguous": {
        "policy_id": "POLICY-006",
        "description": "Gateway marked this payment 'deemed_success' -- assumed settled but "
                        "not yet confirmed by the bank-side settlement file.",
        "typical_cause": "Beneficiary bank didn't respond within the expected window; "
                          "settlement is inferred pending the T+2 settlement file exchange.",
        "resolution_action": "Re-check once the settlement file arrives (typically T+2). "
                              "If confirmed by then, auto-resolve. If still unconfirmed "
                              "past T+2, escalate as a genuine discrepancy.",
        "auto_resolvable": True,
        "risk_class": "medium",
    },
    "unexplained_shortage": {
        "policy_id": "POLICY-007",
        "description": "Bank credit is less than expected, with no refund/adjustment on "
                        "record that explains the gap.",
        "typical_cause": "Undocumented fee, bank-side deduction, or a genuine reconciliation "
                          "break requiring investigation.",
        "resolution_action": "Escalate to finance ops with the exact shortfall amount. "
                              "Do not assume a cause without evidence -- request the bank's "
                              "detailed settlement breakdown.",
        "auto_resolvable": False,
        "risk_class": "high",
    },
    "held_for_risk_review": {
        "policy_id": "POLICY-008",
        "description": "Settlement is withheld pending a risk/fraud review -- no bank "
                        "posting will occur until the review clears.",
        "typical_cause": "Automated risk-scoring flagged the account or transaction pattern.",
        "resolution_action": "Do not attempt reconciliation. This is a deliberate hold, "
                              "not a data error. Route to the risk team; reconciliation "
                              "resumes only after the hold is cleared.",
        "auto_resolvable": False,
        "risk_class": "high",
    },
    "duplicate_payment_detected": {
        "policy_id": "POLICY-009",
        "description": "A second, genuinely separate successful charge exists for the same "
                        "order -- the customer was charged twice.",
        "typical_cause": "Customer double-submitted, or a client-side retry bypassed "
                          "idempotency protection.",
        "resolution_action": "Escalate immediately for a refund of the duplicate charge. "
                              "High customer-impact issue -- do not auto-resolve.",
        "auto_resolvable": False,
        "risk_class": "high",
    },
    "signature_verification_failed": {
        "policy_id": "POLICY-010",
        "description": "The payment's cryptographic signature is invalid -- authenticity "
                        "cannot be confirmed.",
        "typical_cause": "Tampered callback, integration bug, or a potential fraud attempt.",
        "resolution_action": "Escalate to security/fraud team immediately. Never trust "
                              "amount or status claims from an unverified payment.",
        "auto_resolvable": False,
        "risk_class": "high",
    },
    "ambiguous_bank_match": {
        "policy_id": "POLICY-011",
        "description": "Two or more bank candidates are equally plausible matches for this "
                        "settlement -- no evidence (amount, date, account) distinguishes them.",
        "typical_cause": "Coincidentally similar transactions (same merchant, amount, "
                          "adjacent timestamps), or a genuine bank-side data ambiguity.",
        "resolution_action": "Escalate for manual review with full candidate list. Do not "
                              "guess -- request additional distinguishing evidence (exact "
                              "posting time, narration detail) from the bank if available.",
        "auto_resolvable": False,
        "risk_class": "medium",
    },
}


def get_policy(exception_type: str) -> dict:
    """Case-insensitive lookup, since structured-output enum casing isn't
    guaranteed to exactly match (per Claude's structured-outputs docs)."""
    normalized = {k.lower(): v for k, v in POLICY_KB.items()}
    key = exception_type.lower().strip()
    if key not in normalized:
        raise KeyError(f"No policy found for exception_type={exception_type!r}. "
                        f"Known types: {list(POLICY_KB.keys())}")
    return normalized[key]
