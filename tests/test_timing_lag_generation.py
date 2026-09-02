"""
Standalone tests proving a real, found-via-multi-seed-sweep bug in
data_generation/payments.py stays fixed: the `instant` draw (~5% of
payments) used to silently override timing_lag_beyond_t2's own intended
3-5 business-day lag whenever both happened to co-occur, forcing
settle_day = captured_day (same-day settlement) on a payment
ground_truth.csv still labels as late.

    python tests/test_timing_lag_generation.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import random

import pandas as pd

from data_generation.payments import build_payments
from data_generation.utils import add_business_days
from data_generation.validation import _validate_timing_lag_payments


def test_beyond_t2_payments_are_never_instant_even_when_instant_would_otherwise_fire():
    """Forces random.random() to always return a value under the instant
    threshold (0.01 < 0.05) -- i.e. the exact condition that used to make
    EVERY eligible payment instant, including timing_lag_beyond_t2 ones.
    With the fix, every timing_lag_beyond_t2 payment drawn under these
    conditions must still settle strictly after the standard T+2 date --
    proving the exclusion actually holds under the worst-case draw, not
    just usually."""
    real_random = random.random
    random.random = lambda: 0.01  # forces the instant condition's left-hand side True, always
    try:
        payments = build_payments(600)  # large enough that timing_lag_beyond_t2 payments are virtually certain
    finally:
        random.random = real_random

    beyond_t2 = payments[(payments["failure_mode"] == "timing_lag_beyond_t2")
                          & (~payments["is_duplicate_child"])]
    assert len(beyond_t2) > 0, "no timing_lag_beyond_t2 payments were drawn -- test is not exercising the bug at all"

    captured_day = pd.to_datetime(beyond_t2["captured_at"]).dt.date
    standard_t2 = captured_day.apply(lambda d: add_business_days(d, 2))
    not_late = beyond_t2[beyond_t2["settle_day"] <= standard_t2]
    assert len(not_late) == 0, (
        f"{len(not_late)} timing_lag_beyond_t2 payment(s) settled on or before the "
        f"standard T+2 date even after the fix: {not_late['transaction_id'].tolist()}"
    )
    print(f"PASS -- {len(beyond_t2)} timing_lag_beyond_t2 payments drawn under the exact "
          f"forced-instant condition that used to trigger the bug, all genuinely late")

    # And the exclusion is narrow, not a blanket "never instant" regression:
    # ordinary clean payments under the same forced draw ARE still instant.
    clean = payments[(payments["failure_mode"] == "clean") & (~payments["is_duplicate_child"])]
    still_instant = clean[pd.to_datetime(clean["captured_at"]).dt.date == clean["settle_day"]]
    assert len(still_instant) > 0, "the instant path appears to be broken entirely, not just fixed for beyond_t2"
    print(f"PASS -- ordinary payments are still instant-eligible under the same forced draw "
          f"({len(still_instant)} of {len(clean)} clean payments) -- the fix is narrowly scoped")


def test_validation_guard_is_non_vacuous():
    """Proves _validate_timing_lag_payments() would actually catch the bug
    if it ever came back, using a synthetic DataFrame with the exact
    tampered shape (a timing_lag_beyond_t2 payment settling same-day) --
    not just that it passes on already-correct data."""
    tampered = pd.DataFrame([{
        "transaction_id": "trn-tampered-1",
        "failure_mode": "timing_lag_beyond_t2",
        "is_duplicate_child": False,
        "captured_at": pd.Timestamp("2026-07-30 10:00:00"),
        "settle_day": pd.Timestamp("2026-07-30").date(),  # same day -- the bug
    }])
    errors = []
    _validate_timing_lag_payments(tampered, errors)
    assert len(errors) == 1 and "trn-tampered-1" in errors[0], errors
    print("PASS -- the guard correctly flags a synthetic same-day timing_lag_beyond_t2 payment")

    genuinely_late = pd.DataFrame([{
        "transaction_id": "trn-genuine-1",
        "failure_mode": "timing_lag_beyond_t2",
        "is_duplicate_child": False,
        "captured_at": pd.Timestamp("2026-07-30 10:00:00"),
        "settle_day": add_business_days(pd.Timestamp("2026-07-30").date(), 3),
    }])
    errors2 = []
    _validate_timing_lag_payments(genuinely_late, errors2)
    assert errors2 == [], errors2
    print("PASS -- a genuinely late timing_lag_beyond_t2 payment does not false-flag")


ALL_TESTS = [
    test_beyond_t2_payments_are_never_instant_even_when_instant_would_otherwise_fire,
    test_validation_guard_is_non_vacuous,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} timing-lag-generation tests passed.")
