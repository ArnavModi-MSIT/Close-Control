"""
Standalone tests for matching/diagnostics.py's two newest functions --
benford_first_digit_analysis() and optimal_assignment_diagnostic() -- both
added following a competitive scan of peer buildathon submissions. Same
discipline as every other diagnostic in this project: proven correct on
synthetic data built to have a KNOWN answer, then checked for real against
the curated dataset, not just assumed to work because it runs without
raising.

    python tests/test_benford_and_assignment_diagnostics.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

import run_matcher
from matching import config as matching_config
from matching.blocking import build_blocks
from matching.diagnostics import (
    _leading_digits, benford_first_digit_analysis, optimal_assignment_diagnostic,
)
from matching.loaders import load_sources
from matching.settlement_builder import build_settlement_candidates


# --- Benford's Law -------------------------------------------------------

def test_benford_correctly_classifies_known_conforming_and_nonconforming_data():
    """A genuinely log-spread series (5 decades) must score close
    conformity; a series deliberately concentrated on one leading digit
    must score nonconformity -- proves the MAD/verdict machinery itself is
    correct, independent of what this project's own dataset happens to show."""
    rng = np.random.default_rng(0)

    conforming = pd.DataFrame({
        "payment_amount_rupees": 10 ** rng.uniform(1, 6, 5000),
        "merchant_id": "merch_test",
    })
    result = benford_first_digit_analysis(conforming)
    assert result["overall"]["conformity"] in ("close conformity", "acceptable conformity"), result["overall"]
    print(f"PASS -- log-spread synthetic data scores "
          f"'{result['overall']['conformity']}' (MAD={result['overall']['mean_absolute_deviation']})")

    nonconforming = pd.DataFrame({
        "payment_amount_rupees": rng.uniform(9000, 9999, 5000),  # every value starts with digit 9
        "merchant_id": "merch_test",
    })
    result2 = benford_first_digit_analysis(nonconforming)
    assert result2["overall"]["conformity"] == "nonconformity", result2["overall"]
    assert result2["overall"]["observed_proportions"][9] > 0.9
    print(f"PASS -- all-digit-9 synthetic data scores 'nonconformity' "
          f"(MAD={result2['overall']['mean_absolute_deviation']}), not false-classified as conforming")


def test_benford_skips_groups_below_the_minimum_sample_size():
    """A group with fewer rows than config.BENFORD_MIN_SAMPLE_SIZE must be
    skipped entirely (None), never scored -- Nigrini's own guidance is that
    a first-digit verdict on a handful of rows isn't meaningful."""
    rng = np.random.default_rng(1)
    small_n = matching_config.BENFORD_MIN_SAMPLE_SIZE - 1
    df = pd.DataFrame({
        "payment_amount_rupees": 10 ** rng.uniform(1, 6, small_n),
        "merchant_id": "merch_tiny",
    })
    result = benford_first_digit_analysis(df)
    assert result["overall"] is None
    assert result["per_group"] == {}
    assert result["groups_below_min_sample"] == 1
    print(f"PASS -- a group of {small_n} rows (below the "
          f"{matching_config.BENFORD_MIN_SAMPLE_SIZE}-row floor) is skipped, not scored")


def test_leading_digit_extraction_is_correct_at_digit_boundaries():
    """Regression test for a real bug: an earlier round-then-truncate
    implementation reported Rs.1,99,999.99 as leading digit 2 (its
    mantissa, 1.9999999, rounds up to 2.0 before truncation) when the true
    answer is 1 -- a value this dataset's own Rs.150-Rs.2,50,000 range can
    genuinely produce. Also pins the opposite direction (float division
    error just BELOW an integer, e.g. 0.3/0.1 == 2.9999999999999996, whose
    bare floor would be 2 rather than 3), so a future 'simplification' to
    plain floor without the epsilon fails here rather than silently."""
    cases = {
        19.999999: 1, 1.9999999: 1, 199999.99: 1,   # the round-up bug
        0.3: 3, 0.5: 5,                              # float error below an integer
        9.9999999: 9, 999.99: 9, 9999.99: 9,         # the 9/10 boundary
        150.0: 1, 1000.0: 1, 2999.99: 2,             # ordinary values
    }
    got = _leading_digits(pd.Series(list(cases.keys()))).tolist()
    wrong = [(v, g, t) for (v, t), g in zip(cases.items(), got) if g != t]
    assert not wrong, f"leading digit wrong for: {wrong}"
    print(f"PASS -- leading digit correct on all {len(cases)} known-answer boundary cases")


def test_benford_real_dataset_matches_the_verified_generator_property():
    """The real curated dataset's amounts are drawn from a 3-tier UNIFORM
    mixture (data_generation/utils.py's gross_amount()), not a log-uniform
    or multiplicative process -- verified separately via a 200k-draw Monte
    Carlo simulation of that exact generator (reproduces the same ~39%/34%
    digit-1/digit-2 spike) and a control run on genuinely log-spread data
    (correctly scores close conformity, MAD 0.00085, proving the test
    itself is not broken). Pins that real, expected, non-vacuous result:
    if a future change to gross_amount() ever made amounts log-uniform,
    this test FAILS loudly rather than leaving CLAUDE.md's own explanation
    of the nonconformity silently stale."""
    gateway, _, _ = load_sources(run_matcher.DATA_DIR)
    result = benford_first_digit_analysis(gateway)
    assert result["overall"] is not None
    assert result["overall"]["conformity"] == "nonconformity"
    # comfortably past the 0.015 nonconformity band, not marginally over it
    assert result["overall"]["mean_absolute_deviation"] > 0.015
    assert len(result["groups_flagged_nonconformity"]) == 5  # all 5 curated merchants, verified live
    print(f"PASS -- real dataset: MAD={result['overall']['mean_absolute_deviation']}, "
          f"{len(result['groups_flagged_nonconformity'])}/5 merchants flagged, matching the "
          f"generator's own verified uniform-tier property")


# --- Greedy vs. optimal assignment ----------------------------------------

def _make_bank(rows):
    return pd.DataFrame(rows, columns=["bank_txn_id", "credit_amount_rupees"])


def test_optimal_assignment_reports_zero_when_nothing_is_contested():
    """Two settlements whose candidate blocks share NO bank row at all
    cannot possibly have been affected by processing order -- must report
    zero contested settlements, zero disagreements, not just zero by luck."""
    settlements = pd.DataFrame([
        {"settlement_id": "setl_A", "expected_total_rupees": 100.0},
        {"settlement_id": "setl_B", "expected_total_rupees": 200.0},
    ])
    bank = _make_bank([("bank_1", 100.0), ("bank_2", 200.0)])
    blocks = {"setl_A": bank[bank["bank_txn_id"] == "bank_1"],
              "setl_B": bank[bank["bank_txn_id"] == "bank_2"]}
    settlement_matches = pd.DataFrame([
        {"settlement_id": "setl_A", "match_pass": "exact", "matched_bank_txn_ids": ["bank_1"]},
        {"settlement_id": "setl_B", "match_pass": "exact", "matched_bank_txn_ids": ["bank_2"]},
    ])
    result = optimal_assignment_diagnostic(settlements, blocks, settlement_matches, bank)
    assert result["contested_settlements"] == 0
    assert result["disagreements"] == 0
    assert result["disagreement_detail"] == []
    print("PASS -- non-overlapping settlements correctly report zero contested, zero disagreements")


def test_optimal_assignment_detects_a_real_disagreement_and_measures_the_improvement():
    """Constructs the textbook case processing order can get wrong: two
    settlements, two shared candidate bank rows, where a (hand-crafted,
    deliberately suboptimal) greedy assignment picks the WRONG row for
    each settlement even though the right pairing was available. Proves
    the diagnostic both detects the disagreement and correctly measures
    that the optimal pairing would have reduced the total delta -- not
    just found *a* difference, but the *right* one."""
    settlements = pd.DataFrame([
        {"settlement_id": "setl_A", "expected_total_rupees": 100.0},
        {"settlement_id": "setl_B", "expected_total_rupees": 200.0},
    ])
    bank = _make_bank([("bank_1", 100.5), ("bank_2", 199.5)])  # bank_1 clearly belongs to A, bank_2 to B
    both = bank  # both settlements see both candidates in their block
    blocks = {"setl_A": both, "setl_B": both}
    # Greedy (simulating an unlucky processing order) assigned the WRONG row to each.
    settlement_matches = pd.DataFrame([
        {"settlement_id": "setl_A", "match_pass": "exact", "matched_bank_txn_ids": ["bank_2"]},
        {"settlement_id": "setl_B", "match_pass": "exact", "matched_bank_txn_ids": ["bank_1"]},
    ])
    result = optimal_assignment_diagnostic(settlements, blocks, settlement_matches, bank)
    assert result["contested_settlements"] == 2
    assert result["components_analyzed"] == 1
    assert result["disagreements"] == 2, result
    assert all(d["optimal_actually_better"] for d in result["disagreement_detail"])
    assert result["optimal_total_delta_rupees"] < result["greedy_total_delta_rupees"]
    print(f"PASS -- detected {result['disagreements']} real disagreement(s); optimal total delta "
          f"Rs.{result['optimal_total_delta_rupees']} < greedy's Rs.{result['greedy_total_delta_rupees']}, "
          f"correctly measured as an improvement, not just a different pairing")


def test_optimal_assignment_real_dataset():
    """Runs the real diagnostic against the real curated dataset end to
    end (rebuilding blocks/settlements exactly as evaluate.py's own 1g
    section does) -- confirms it completes without error and returns a
    structurally sane result. Not pinned to an exact disagreement count
    (that number is a genuine property of the real matcher's current
    behavior, reported honestly in evaluate.py's own output, not
    hardcoded here as a brittle regression target)."""
    gateway, bank, ledger = load_sources(run_matcher.DATA_DIR)
    settlements = build_settlement_candidates(gateway)
    blocks = build_blocks(settlements, bank)
    report, settlement_matches, ledger_check = run_matcher.run()
    result = optimal_assignment_diagnostic(settlements, blocks, settlement_matches, bank)
    assert result["contested_settlements"] >= 0
    assert result["disagreements"] <= result["contested_settlements"]
    assert result["optimal_total_delta_rupees"] <= result["greedy_total_delta_rupees"] + 0.01
    print(f"PASS -- real dataset: {result['contested_settlements']} contested settlement(s), "
          f"{result['disagreements']} disagreement(s) ({result['disagreement_rate_pct']}%)")


ALL_TESTS = [
    test_benford_correctly_classifies_known_conforming_and_nonconforming_data,
    test_benford_skips_groups_below_the_minimum_sample_size,
    test_leading_digit_extraction_is_correct_at_digit_boundaries,
    test_benford_real_dataset_matches_the_verified_generator_property,
    test_optimal_assignment_reports_zero_when_nothing_is_contested,
    test_optimal_assignment_detects_a_real_disagreement_and_measures_the_improvement,
    test_optimal_assignment_real_dataset,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} Benford/assignment diagnostic tests passed.")
