"""
Standalone proof that the engine's ambiguity detection actually fires.

The main dataset currently has zero genuinely ambiguous bank-candidate
cases (verified: had_ambiguous_candidates=0 across all 176 settlements),
so the mechanism exists in engine.py but was never exercised end-to-end.
Rather than restructuring the generator to inject adversarial cases
(a larger, separate change), this constructs two minimal synthetic
scenarios directly and runs them through the real matching engine.

    python tests/test_ambiguity.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import sys

import pandas as pd

from matching.engine import run_matching

# Windows consoles default to the cp1252 codepage, which can't encode the
# rupee sign (U+20B9) used below -- force UTF-8 stdout so this runs the same
# on Windows as everywhere else, without requiring PYTHONIOENCODING=utf-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def scenario_exact_single_vs_split():
    """Settlement expects 1000.00. Bank has ONE row at exactly 1000.00
    AND two other rows (400.00 + 600.00) that also sum to exactly 1000.00.
    Both are structurally valid -- the engine must not silently pick one."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST1", "merchant_id": "merch_test",
        "member_count": 3, "expected_total_rupees": 1000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_A", "utr": "utrA", "credit_amount_rupees": 1000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_B", "utr": "utrB", "credit_amount_rupees": 400.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_C", "utr": "utrC", "credit_amount_rupees": 600.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST1": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 1: exact single (₹1000) vs exact split (₹400+₹600)")
    print(f"  match_status = {row.match_status}  (expected: ambiguous)")
    print(f"  match_pass   = {row.match_pass}")
    assert row.match_status == "ambiguous", "FAILED: engine picked one arbitrarily instead of escalating"
    print("  PASS -- engine correctly escalated instead of guessing.\n")


def scenario_multiple_valid_splits():
    """Settlement expects 10000.00. Bank has FOUR rows: 4000+6000=10000
    AND 4100+5900=10000 -- two independent, equally valid decompositions."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST2", "merchant_id": "merch_test",
        "member_count": 4, "expected_total_rupees": 10000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_D", "utr": "utrD", "credit_amount_rupees": 4000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_E", "utr": "utrE", "credit_amount_rupees": 6000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_F", "utr": "utrF", "credit_amount_rupees": 4100.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_G", "utr": "utrG", "credit_amount_rupees": 5900.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST2": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 2: two independent valid splits (4000+6000 AND 4100+5900)")
    print(f"  match_status = {row.match_status}  (expected: ambiguous)")
    print(f"  match_pass   = {row.match_pass}")
    assert row.match_status == "ambiguous", "FAILED: engine picked one pair arbitrarily instead of escalating"
    print("  PASS -- engine correctly escalated instead of guessing.\n")


def scenario_unambiguous_control():
    """Control case: no competing decomposition should still match cleanly."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST3", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 500.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_H", "utr": "utrH", "credit_amount_rupees": 500.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST3": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 3 (control): single unambiguous candidate")
    print(f"  match_status = {row.match_status}  (expected: matched)")
    assert row.match_status == "matched", "FAILED: control case should match cleanly, ambiguity check is too aggressive"
    print("  PASS -- unambiguous case still resolves normally (no false-positive escalation).\n")


def scenario_exact_vs_exact():
    """Two candidates both exactly match expected (0 delta vs 0 delta) --
    the relative-difference tie test can never catch this case, so it
    needed an explicit exact-tolerance check."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST4", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 1000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_I", "utr": "utrI", "credit_amount_rupees": 1000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_J", "utr": "utrJ", "credit_amount_rupees": 1000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST4": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 4: two candidates both exactly ₹1000 (0 delta vs 0 delta)")
    print(f"  match_status = {row.match_status}  (expected: ambiguous)")
    print(f"  match_pass   = {row.match_pass}")
    assert row.match_status == "ambiguous", "FAILED: engine picked one of two identical exact candidates arbitrarily"
    print("  PASS -- engine correctly escalated instead of guessing.\n")


def scenario_ambiguous_shortage():
    """Two candidates equally plausible as a shortage match -- the engine
    must not silently consume one just because confidence was 'medium'."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST5", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 10000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_K", "utr": "utrK", "credit_amount_rupees": 9000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_L", "utr": "utrL", "credit_amount_rupees": 9000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST5": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 5: two equal shortage candidates (₹9000 vs ₹9000, expected ₹10000)")
    print(f"  match_status = {row.match_status}  (expected: ambiguous)")
    print(f"  match_pass   = {row.match_pass}")
    assert row.match_status == "ambiguous", "FAILED: engine consumed one of two tied shortage candidates arbitrarily"
    print("  PASS -- engine correctly escalated instead of guessing.\n")


def scenario_ambiguous_overage():
    """Same as shortage, mirrored above expected."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST6", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 10000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_M", "utr": "utrM", "credit_amount_rupees": 11000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
        {"bank_txn_id": "bnk_N", "utr": "utrN", "credit_amount_rupees": 11000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST6": bank}
    result = run_matching(settlements, blocks, bank)
    row = result.iloc[0]
    print("Scenario 6: two equal overage candidates (₹11000 vs ₹11000, expected ₹10000)")
    print(f"  match_status = {row.match_status}  (expected: ambiguous)")
    print(f"  match_pass   = {row.match_pass}")
    assert row.match_status == "ambiguous", "FAILED: engine consumed one of two tied overage candidates arbitrarily"
    print("  PASS -- engine correctly escalated instead of guessing.\n")


def scenario_shortage_tolerant_single():
    """A SINGLE plausibly-short candidate -- the non-ambiguous shortage path.

    Distinct from scenario 5: there the two tied candidates make it
    ambiguous. Here exactly one candidate sits inside the shortage band, so
    the engine should MATCH it and mark the result an exception (bank paid
    less than the settlement expected) rather than escalate or drop it.

    Never fires on the curated dataset -- every bank posting there equals
    its settlement total exactly, so `shortage_tolerant` had no coverage
    from data OR tests until this scenario existed, while
    run_baseline_naive.py's output credited it for value it wasn't
    demonstrably providing."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST8", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 10000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    # 9,500 = 95% of expected: inside SHORTAGE_TOLERANCE_MIN_FRACTION (0.90),
    # below the exact-match tolerance, and the only candidate in the block.
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_S1", "utr": "utrS1", "credit_amount_rupees": 9500.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    result = run_matching(settlements, {"setl_TEST8": bank}, bank)
    row = result.iloc[0]
    print("Scenario 8: one plausibly-short candidate (₹9,500 vs expected ₹10,000)")
    print(f"  match_status = {row.match_status}  (expected: matched_with_exception)")
    print(f"  match_pass   = {row.match_pass}  (expected: shortage_tolerant)")
    print(f"  delta        = {row.amount_delta_rupees}")
    assert row.match_status == "matched_with_exception", \
        f"FAILED: expected matched_with_exception, got {row.match_status}"
    assert row.match_pass == "shortage_tolerant", \
        f"FAILED: expected the shortage_tolerant pass, got {row.match_pass}"
    print("  PASS -- matched, and correctly flagged as an exception not a clean match.\n")


def scenario_overage_tolerant_single():
    """Mirror of scenario 8, above expected: the bank credited MORE than the
    settlement called for. Also never fires on the curated dataset."""
    settlements = pd.DataFrame([{
        "settlement_id": "setl_TEST9", "merchant_id": "merch_test",
        "member_count": 1, "expected_total_rupees": 10000.00,
        "settle_date": pd.Timestamp("2026-07-01").date(),
    }])
    # 10,800 = 108% of expected: inside OVERAGE_TOLERANCE_MAX_FRACTION (1.15).
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_O1", "utr": "utrO1", "credit_amount_rupees": 10800.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    result = run_matching(settlements, {"setl_TEST9": bank}, bank)
    row = result.iloc[0]
    print("Scenario 9: one over-credited candidate (₹10,800 vs expected ₹10,000)")
    print(f"  match_status = {row.match_status}  (expected: matched_with_exception)")
    print(f"  match_pass   = {row.match_pass}  (expected: overage_tolerant)")
    print(f"  bank_overage = {row.bank_overage}")
    assert row.match_status == "matched_with_exception", \
        f"FAILED: expected matched_with_exception, got {row.match_status}"
    assert row.match_pass == "overage_tolerant", \
        f"FAILED: expected the overage_tolerant pass, got {row.match_pass}"
    assert bool(row.bank_overage), "FAILED: bank_overage flag not set on an over-credit"
    print("  PASS -- matched, flagged as exception, overage flag set.\n")


def scenario_cross_settlement_conflict():
    """Two DIFFERENT settlements both expect exactly ₹1000, and only ONE
    bank posting of ₹1000 exists. Documents current (greedy, order-
    dependent) behavior rather than asserting an ideal outcome -- this is
    a known, accepted limitation, not something this test claims to fix."""
    settlements = pd.DataFrame([
        {"settlement_id": "setl_TEST7A", "merchant_id": "merch_test",
         "member_count": 1, "expected_total_rupees": 1000.00,
         "settle_date": pd.Timestamp("2026-07-01").date()},
        {"settlement_id": "setl_TEST7B", "merchant_id": "merch_test",
         "member_count": 1, "expected_total_rupees": 1000.00,
         "settle_date": pd.Timestamp("2026-07-01").date()},
    ])
    bank = pd.DataFrame([
        {"bank_txn_id": "bnk_O", "utr": "utrO", "credit_amount_rupees": 1000.00,
         "credit_date": pd.Timestamp("2026-07-01").date(), "bank_account_id": "acct_merch_test"},
    ])
    blocks = {"setl_TEST7A": bank, "setl_TEST7B": bank}
    result = run_matching(settlements, blocks, bank)
    matched = result[result.match_status == "matched"]
    unmatched = result[result.match_status == "unmatched"]
    print("Scenario 7: two settlements, one shared bank candidate (documents current behavior)")
    print(f"  matched: {len(matched)}, unmatched: {len(unmatched)}")
    assert len(matched) == 1 and len(unmatched) == 1, \
        "Unexpected outcome -- greedy consumption behavior changed, re-verify this is still acceptable"
    print("  Confirmed: one settlement wins (processing order), the other is")
    print("  correctly left unmatched (NOT double-counted or falsely matched).")
    print("  KNOWN LIMITATION: outcome depends on settle_date/settlement_id")
    print("  ordering, not on which is 'more correct'. Documented, not fixed --")
    print("  would need a global assignment solver to resolve properly.\n")


if __name__ == "__main__":
    scenario_exact_single_vs_split()
    scenario_multiple_valid_splits()
    scenario_unambiguous_control()
    scenario_exact_vs_exact()
    scenario_ambiguous_shortage()
    scenario_ambiguous_overage()
    scenario_shortage_tolerant_single()
    scenario_overage_tolerant_single()
    scenario_cross_settlement_conflict()
    print("All ambiguity-mechanism proofs passed.")
