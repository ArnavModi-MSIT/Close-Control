"""
Standalone unit tests for investigator/tools.py's near-miss fallback on
search_bank_statement() -- when the strict window+tolerance search finds
nothing at all, does the tool look wider and explain the closest thing it
found instead of just returning an empty candidate list?

Runs entirely offline against small synthetic ToolContexts (no real demo
data, no live Ollama) -- same reasoning test_adversarial_injection.py
gives for its own synthetic contexts.

    python tests/test_investigator_tools.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from investigator.tools import ToolContext, search_bank_statement


def _ctx(bank_rows: list[dict], expected_net: float, settlement_matches=None):
    gateway = pd.DataFrame([{
        "transaction_id_ref": "trn-nm-1", "attempt_status": "success",
        "merchant_id": "mer-nm-1", "order_id": "ord-nm-1", "payment_method": "upi",
        "captured_at": pd.Timestamp("2026-07-15"), "payment_amount_rupees": expected_net,
        "fee_rupees": 0.0, "tax_rupees": 0.0, "settlement_id": None, "status": "captured",
        "signature_valid": True, "refund_id": None, "refund_reason": None,
        "adjustment_rupees": 0.0,
    }])
    bank = pd.DataFrame(bank_rows)
    report = pd.DataFrame([{
        "transaction_id": "trn-nm-1", "final_exception_type": "missing_bank_reference",
        "settlement_id": None, "ledger_expected_net_rupees": expected_net,
        "observed_net_rupees": None, "net_delta_rupees": None,
        "is_clean": False, "risk_class": "medium",
    }])
    return ToolContext(report=report, gateway=gateway, bank=bank,
                        settlement_matches=settlement_matches)


def test_strict_match_found_no_near_miss_field_at_all():
    """When the strict search already finds a real candidate, `near_miss`
    must not even be present as a key -- a caller checking `"near_miss" in
    result` must not confuse "we found a real match" with "we looked
    further and still found nothing."."""
    ctx = _ctx([{
        "bank_txn_id": "bank-nm-1", "utr": "utr-nm-1", "credit_amount_rupees": 1000.0,
        "credit_date": "2026-07-16", "bank_account_id": "acc-nm-1", "narration": "ok",
    }], expected_net=1000.0)
    result = search_bank_statement(ctx, "trn-nm-1")
    assert result["candidate_count"] == 1
    assert "near_miss" not in result
    print("PASS -- a real strict-search match carries no near_miss field at all")


def test_near_miss_found_when_strict_search_empty():
    """Nothing within the strict +/-5 day / Rs.5 window, but a real
    candidate sits 12 days late and Rs.340 short within the wider 30-day
    net -- the near-miss fallback must find it and explain both gaps."""
    ctx = _ctx([{
        "bank_txn_id": "bank-nm-2", "utr": "utr-nm-2", "credit_amount_rupees": 660.0,
        "credit_date": "2026-07-27", "bank_account_id": "acc-nm-1", "narration": "late one",
    }], expected_net=1000.0)
    result = search_bank_statement(ctx, "trn-nm-1")
    assert result["candidate_count"] == 0
    assert "near_miss" in result
    nm = result["near_miss"]
    assert nm["bank_txn_id"] == "bank-nm-2"
    assert nm["amount_diff_rupees"] == 340.0
    assert nm["date_diff_days"] == 12
    assert nm["candidate_status"] == "unclaimed"
    assert "340.00" in nm["explanation"] and "12 day" in nm["explanation"]
    print("PASS -- a real near-miss (late + short) is found and correctly explained")


def test_near_miss_reports_already_claimed_status():
    """A near-miss candidate consumed by a DIFFERENT settlement's real
    match must say so -- exactly the same distinction the strict search's
    own candidate_status already makes, applied here too."""
    ctx = _ctx([{
        "bank_txn_id": "bank-nm-3", "utr": "utr-nm-3", "credit_amount_rupees": 990.0,
        "credit_date": "2026-07-20", "bank_account_id": "acc-nm-1", "narration": "claimed",
    }], expected_net=1000.0)
    ctx.claimed_bank_txn_ids.add("bank-nm-3")
    result = search_bank_statement(ctx, "trn-nm-1")
    nm = result["near_miss"]
    assert nm["candidate_status"] == "already_matched_elsewhere"
    assert "already claimed" in nm["explanation"]
    print("PASS -- a near-miss already consumed by another settlement is correctly labeled")


def test_no_near_miss_when_nothing_within_30_days_either():
    """A candidate 45 days away is outside even the widened net -- the
    fallback must not report it as a "near miss" (that would misrepresent
    a genuinely unrelated posting as a real lead), and near_miss stays
    absent from the response entirely, same as the real-match case."""
    ctx = _ctx([{
        "bank_txn_id": "bank-nm-4", "utr": "utr-nm-4", "credit_amount_rupees": 1000.0,
        "credit_date": "2026-08-29", "bank_account_id": "acc-nm-1", "narration": "too far",
    }], expected_net=1000.0)
    result = search_bank_statement(ctx, "trn-nm-1")
    assert result["candidate_count"] == 0
    assert "near_miss" not in result
    print("PASS -- a candidate outside even the widened 30-day net is not reported as a near miss")


def test_near_miss_picks_closest_amount_not_closest_date():
    """Two candidates exist outside the strict window: one is same-day but
    wildly wrong in amount (a different merchant's coincidental posting),
    the other is a week off but the exact right amount. The nearer-by
    -amount one must win -- the same rupee figure posted late is far more
    likely to be the real payment than a same-day coincidence."""
    ctx = _ctx([
        {"bank_txn_id": "bank-nm-5a", "utr": "utr-5a", "credit_amount_rupees": 50.0,
         "credit_date": "2026-07-21", "bank_account_id": "acc-nm-1", "narration": "same day, wrong amount"},
        {"bank_txn_id": "bank-nm-5b", "utr": "utr-5b", "credit_amount_rupees": 1000.0,
         "credit_date": "2026-07-28", "bank_account_id": "acc-nm-1", "narration": "a week late, exact amount"},
    ], expected_net=1000.0)
    result = search_bank_statement(ctx, "trn-nm-1")
    assert result["near_miss"]["bank_txn_id"] == "bank-nm-5b"
    assert result["near_miss"]["amount_diff_rupees"] == 0.0
    print("PASS -- near-miss ranks by amount closeness, not date closeness")


ALL_TESTS = [
    test_strict_match_found_no_near_miss_field_at_all,
    test_near_miss_found_when_strict_search_empty,
    test_near_miss_reports_already_claimed_status,
    test_no_near_miss_when_nothing_within_30_days_either,
    test_near_miss_picks_closest_amount_not_closest_date,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} investigator-tools tests passed.")
