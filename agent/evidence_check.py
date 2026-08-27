"""Evidence completeness check. The agent should never be asked to guess
when the deterministic pipeline hasn't actually produced enough evidence
to support a conclusion -- this check runs BEFORE any LLM call and short-
circuits straight to an honest 'insufficient_evidence' result if the
required fields are missing.
"""

REQUIRED_EVIDENCE = {
    "missing_bank_reference": ["settlement_id", "match_status"],
    "partial_refund": ["net_delta_rupees", "ledger_expected_net_rupees", "observed_net_rupees"],
    "fee_variance": ["net_delta_rupees", "ledger_expected_net_rupees", "observed_net_rupees"],
    "unexplained_shortage": ["net_delta_rupees", "ledger_expected_net_rupees", "observed_net_rupees"],
    "duplicate_payment_detected": ["transaction_id", "all_signals"],
    "signature_verification_failed": ["transaction_id"],
    "held_for_risk_review": [],  # settlement_id is structurally always absent for
                                   # this type BY DESIGN (settlement never occurs
                                   # while held) -- requiring it would incorrectly
                                   # short-circuit every single case before the
                                   # agent ever sees it. The absence itself IS the
                                   # evidence, not a gap in it.
    "deemed_success_ambiguous": ["match_status", "settlement_id"],
    "ambiguous_bank_match": ["settlement_id", "match_status"],
    "timing_lag_beyond_t2": ["settlement_id"],
    "duplicate_retry": [],
}


def check_evidence_complete(report_row: dict) -> tuple[bool, list[str]]:
    """Returns (is_complete, missing_fields)."""
    exc_type = report_row.get("final_exception_type")
    required = REQUIRED_EVIDENCE.get(exc_type, [])
    missing = [
        field for field in required
        if report_row.get(field) is None or report_row.get(field) == ""
    ]
    return len(missing) == 0, missing
