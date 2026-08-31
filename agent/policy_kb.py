"""Policy knowledge base. Each rule is what the agent retrieves and must
cite (policy_id) before proposing a resolution. Deliberately a small,
readable lookup table -- not a vector DB -- since the exception taxonomy
is fixed and small; a KB this size needs retrieval-by-key, not semantic
search.

auto_resolvable here means "the policy, in principle, permits an
autonomous system to resolve this type" -- the actual auto-resolve
decision is still gated by confidence + risk ceiling in gate.py. This
field alone never authorizes anything.

POLICY-007 and POLICY-009's resolution_action text cites real RBI/NPCI
regulatory frameworks (not invented), added after checking each claim
against actual circulars/SOPs rather than trusting a generic web summary:
  - RBI's Harmonisation of TAT and Customer Compensation for Failed
    Transactions circular (20.09.2019): T+5 business days is the outer
    auto-reversal bound for a failed/short transaction, with Rs.100/day
    compensation owed automatically (suo moto, no customer complaint
    needed) past that.
  - RBI's DGBA.GBD circular (02.08.2021) on recovery of interest on
    excess put-through/double-claim government transactions: an excess
    or duplicate payment accrues penal interest for every day it's held
    -- amount x days-held x rate / 365 -- calculated from the day after
    the T+5 put-through deadline until actual reversal. Confirmed via a
    real government treasury-reconciliation SOP's own worked example
    (Working Sheet -- Penal Interest), which independently uses the same
    T+5 deadline as the RBI TAT circular above -- two separate sources
    agreeing on the same number, not one claim taken on faith.
  - NPCI's URCS (UPI Reconciliation and Chargeback System) gives
    customers a 45-day window to raise a dispute and independently
    flags duplicate adjustments (by transaction ID/amount) before a
    chargeback is even accepted.
These are real regulatory anchors for what "escalate" should actually
mean operationally, not just a generic instruction -- but they don't
change auto_resolvable/risk_class here, since those still come from
this project's own deterministic gate design, not from citing a circular.
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
                              "detailed settlement breakdown. RBI's harmonised TAT framework "
                              "for failed/short transactions sets T+5 business days as the "
                              "outer resolution bound, with Rs.100/day compensation owed "
                              "automatically past that -- treat T+5 as the hard escalation "
                              "deadline, not an arbitrary one.",
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
                              "RBI's framework for excess/double-claim payments (DGBA.GBD "
                              "circular, recovery of interest on excess put-through) treats "
                              "the holding period as penal-interest-bearing -- amount x days "
                              "held x rate / 365 from T+5 until reversed -- a real, "
                              "escalating-cost liability, not just a customer-service issue. "
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
    "chargeback_received": {
        "policy_id": "POLICY-012",
        "description": "The customer's issuing bank has raised a dispute and pulled the "
                        "funds back from the merchant -- money that already settled is "
                        "being reversed, not merely adjusted.",
        "typical_cause": "Customer-initiated dispute (goods not received, unauthorized "
                          "transaction, duplicate charge), raised through the issuer and "
                          "routed via NPCI's URCS.",
        "resolution_action": "Never auto-resolve; never net against the original settlement. "
                              "Route to the disputes team with the transaction evidence bundle "
                              "before the representment deadline. NPCI's URCS gives the "
                              "customer a 45-day window to raise the dispute and independently "
                              "screens duplicate adjustments before accepting one, so verify "
                              "against URCS rather than trusting the debit in isolation -- a "
                              "chargeback and the refund it may duplicate are two different "
                              "debits for the same underlying transaction.",
        "auto_resolvable": False,
        "risk_class": "high",
    },
    "loan_recovery_deduction": {
        "policy_id": "POLICY-013",
        "description": "The settlement credited less than the ledger expected because a "
                        "contracted Razorpay Capital advance was repaid by deducting an "
                        "agreed percentage of this settlement. The shortfall is a "
                        "collection, not a loss -- but only when Capital's recovery "
                        "ledger accounts for the delta in full.",
        "typical_cause": "An active working-capital advance whose repayment terms collect "
                          "a fixed share of each settlement, applied by Capital after the "
                          "settlement ledger had already booked the pre-recovery net.",
        "resolution_action": "Auto-resolve ONLY when a recovery record exists for this "
                              "transaction and its amount reconciles the observed delta "
                              "exactly; a recovery that explains only part of the gap "
                              "leaves a genuinely unexplained residual and must escalate. "
                              "RBI's Guidelines on Digital Lending (02.09.2022) require "
                              "loan repayments to move directly between the borrower and "
                              "the regulated lending entity's accounts, with no "
                              "pass-through or pool account held by a lending service "
                              "provider -- so confirm the recovery is booked against the "
                              "lender's account rather than retained in an aggregator "
                              "float. The deduction must also match the rate disclosed in "
                              "the Key Fact Statement and the Fair Practices Code "
                              "disclosure the merchant accepted; an undisclosed or "
                              "off-schedule deduction is a compliance exception, not a "
                              "reconciliation one.",
        "auto_resolvable": True,
        "risk_class": "low",
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
