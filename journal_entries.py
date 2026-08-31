"""Deterministic double-entry journal-entry drafting -- "run the books,"
taken literally, on top of matching/'s already-computed reconciliation
report. Idea sharpened by an external repo scan (Ledgermind-AI, a peer
buildathon submission): its README described an LLM drafting proposed
GL journal entries per exception type; this project's own "AI proposes,
deterministic code disposes" rule pushes that further -- the accounting
TREATMENT for a given exception_type is fixed, known, standard practice,
not a case-by-case judgment call, so there is nothing here for an LLM to
usefully decide. Every line, every account, every rupee figure is plain
Python over fields matching/report.py already computed. No LLM call, no
network dependency, no latency -- available instantly for every case,
unlike qa_agent/'s multi-second Ollama round trips.

Same boundary as everywhere else in this project: this DRAFTS a proposed
entry for a human to review and post in a real GL, exactly like
agent/'s "drafted_communication" field. Nothing here posts anything,
touches a case's status, or is trusted without the balance check below.
"""

import datetime as dt

CHART_OF_ACCOUNTS = {
    "1010": "Bank Account",
    "1200": "Gateway Settlement Receivable",
    "1900": "Reconciliation Suspense -- Pending Investigation",
    "2100": "Razorpay Capital -- Loan Payable",
    "5100": "Payment Processing Fee Expense",
    "5200": "Refunds & Returns",
    "5300": "Chargeback Loss Expense",
    "5400": "Duplicate Payment -- Clearing/Suspense",
    "5900": "Other Settlement Variance",
}

# Which account absorbs the variance between what the ledger expected and
# what was actually observed, keyed by the matcher's own final_exception_type.
# Purely a categorization choice for human readability -- it does NOT
# affect whether the entry balances (see build_journal_entry()'s
# universal template, which balances by construction regardless of which
# account this points at). None (the dict key, not a Python None
# variance) means "clean" or a genuinely zero-delta case -- no variance
# line is added at all.
VARIANCE_ACCOUNT_BY_EXCEPTION_TYPE = {
    None: None,                                   # clean
    "timing_lag_beyond_t2": None,                  # money is correct, just late -- no true variance
    "fee_variance": "5100",
    "loan_recovery_deduction": "2100",             # a contracted recovery reduces a real liability,
                                                     # not a generic expense -- the one type where the
                                                     # variance account is a genuine balance-sheet
                                                     # account, not an income-statement one
    "partial_refund": "5200",
    "chargeback_received": "5300",
    "duplicate_payment_detected": "5400",
    # Every other exception type reaching this point has a genuinely
    # unknown cause as far as the deterministic layer is concerned (that
    # is exactly why the matcher escalated it rather than auto-resolving)
    # -- routed to Suspense rather than guessing at a specific GL
    # treatment the data does not actually support yet. Explicit "escalate,
    # don't guess" discipline, applied to accounting the same way it is
    # applied everywhere else in this project.
}

_DEFAULT_VARIANCE_ACCOUNT = "1900"


def _round(x) -> float:
    return round(float(x), 2)


def build_journal_entry(report_row: dict) -> dict:
    """One proposed double-entry journal entry for a single ledger
    transaction, built entirely from fields matching/report.py already
    computed (observed_net_rupees, ledger_expected_net_rupees,
    net_delta_rupees, final_exception_type). Balances by construction --
    see the algebra in the comments below -- but validate_balanced() still
    checks it explicitly rather than trusting the construction blindly,
    the same "prove it, don't just assert it" discipline this project
    applies to every other invariant (verify_consumption_invariants(),
    _assert_partition(), etc.).

    held_for_risk_review is a deliberate special case: its
    observed_net_rupees is NOT a real bank observation (matches
    cash_position/engine.py's own documented caveat -- it's a theoretical
    gateway computation that "looks plausible" even though no settlement
    or bank posting has actually occurred for a held case). Debiting Bank
    for that figure would claim money arrived that never did, so the
    FULL expected amount routes to Suspense instead, and Bank is not
    touched at all.
    """
    txn_id = report_row["transaction_id"]
    exception_type = report_row.get("final_exception_type")
    expected = _round(report_row.get("ledger_expected_net_rupees") or 0.0)
    observed = report_row.get("observed_net_rupees")
    delta = report_row.get("net_delta_rupees")

    lines = []

    if exception_type == "held_for_risk_review" or observed is None:
        # Nothing has genuinely settled -- the entire expected amount is
        # still in limbo, not in the bank. A single suspense line, no
        # Bank line, no variance line (there is nothing to compare
        # observed against yet).
        lines.append({"account_code": "1900", "account_name": CHART_OF_ACCOUNTS["1900"],
                       "side": "DR", "amount_rupees": expected})
        lines.append({"account_code": "1200", "account_name": CHART_OF_ACCOUNTS["1200"],
                       "side": "CR", "amount_rupees": expected})
    else:
        observed = _round(observed)
        delta = _round(delta) if delta is not None else _round(observed - expected)
        lines.append({"account_code": "1010", "account_name": CHART_OF_ACCOUNTS["1010"],
                       "side": "DR", "amount_rupees": observed})

        if abs(delta) > 0.005:
            variance_code = VARIANCE_ACCOUNT_BY_EXCEPTION_TYPE.get(exception_type, _DEFAULT_VARIANCE_ACCOUNT)
            if variance_code is None:
                variance_code = _DEFAULT_VARIANCE_ACCOUNT
            # delta = observed - expected (matching/ledger_check.py's own
            # definition). delta < 0 means less arrived than expected --
            # that shortfall is a real DEBIT (an expense, or a liability
            # reduction for loan_recovery_deduction) that, together with
            # observed, must sum to expected on the credit side. delta > 0
            # means MORE arrived than expected -- an overage, credited as
            # other income/variance.
            if delta < 0:
                lines.append({"account_code": variance_code, "account_name": CHART_OF_ACCOUNTS[variance_code],
                               "side": "DR", "amount_rupees": _round(abs(delta))})
            else:
                lines.append({"account_code": variance_code, "account_name": CHART_OF_ACCOUNTS[variance_code],
                               "side": "CR", "amount_rupees": _round(delta)})

        lines.append({"account_code": "1200", "account_name": CHART_OF_ACCOUNTS["1200"],
                       "side": "CR", "amount_rupees": expected})

    narration = _narration(txn_id, exception_type, expected, observed, delta)
    total_dr = _round(sum(l["amount_rupees"] for l in lines if l["side"] == "DR"))
    total_cr = _round(sum(l["amount_rupees"] for l in lines if l["side"] == "CR"))

    return {
        "transaction_id": txn_id,
        "exception_type": exception_type,
        "narration": narration,
        "lines": lines,
        "total_debits_rupees": total_dr,
        "total_credits_rupees": total_cr,
        "balanced": validate_balanced({"lines": lines}),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _narration(txn_id, exception_type, expected, observed, delta) -> str:
    if exception_type is None:
        return f"{txn_id}: settlement confirmed, Rs.{expected:,.2f}, no exception."
    if exception_type == "held_for_risk_review" or observed is None:
        return (f"{txn_id}: held for risk review -- Rs.{expected:,.2f} expected, "
                f"not yet settled. Held in suspense pending resolution.")
    if abs(delta) <= 0.005:
        return f"{txn_id}: settled as expected, Rs.{expected:,.2f} ({exception_type})."
    return (f"{txn_id} ({exception_type}): expected Rs.{expected:,.2f}, observed "
            f"Rs.{observed:,.2f}, delta Rs.{delta:,.2f}.")


def validate_balanced(entry: dict, tolerance_rupees: float = 0.02) -> bool:
    """The one hard invariant a journal entry must satisfy before it is
    fit to show a reviewer, let alone post: total debits == total
    credits. Same tolerance as matching.config.EXACT_MATCH_TOLERANCE_RUPEES
    (paisa-level rounding only) -- deliberately NOT reused by import here,
    since journal_entries.py has no other dependency on matching/ and a
    literal 0.02 rupee tolerance is self-evidently correct at this scale
    without needing matching/'s own reasoning imported alongside it."""
    total_dr = sum(l["amount_rupees"] for l in entry["lines"] if l["side"] == "DR")
    total_cr = sum(l["amount_rupees"] for l in entry["lines"] if l["side"] == "CR")
    return abs(total_dr - total_cr) <= tolerance_rupees
