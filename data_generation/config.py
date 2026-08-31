"""Configuration and constants for the synthetic reconciliation dataset."""

import os
import random
import datetime as dt

import numpy as np

DATASET_VERSION = "2.1"
# Overridable ONLY for a seed-robustness check --
# regenerating the full dataset under a different seed into a separate
# --out-dir, to prove the matcher/gate's hardcoded thresholds aren't
# accidentally tuned to seed=42's specific random draws. Never set this to
# regenerate the curated demo dataset itself; the default (42) is what
# every other verified number in this project is measured against.
RNG_SEED = int(os.environ.get("RNG_SEED_OVERRIDE", 42))

# Seed once, at import time, before any other module can draw randomness.
random.seed(RNG_SEED)
np.random.seed(RNG_SEED)

N_PAYMENTS = 2000
BATCH_START = dt.date(2026, 7, 1)
BATCH_DAYS = 30

MERCHANTS = [
    ("merch_001", "Kunal Fashions Pvt Ltd"),
    ("merch_002", "Bright Electronics"),
    ("merch_003", "Spice Route Foods"),
    ("merch_004", "UrbanNest Furnishings"),
    ("merch_005", "PixelCraft Studio"),
]

PAYMENT_METHODS = {
    # method: (MDR %, weight)
    "upi": (0.0, 0.42),
    "rupay_debit": (0.0, 0.10),
    "debit_card": (0.009, 0.13),
    "credit_card": (0.018, 0.20),
    "netbanking": (0.010, 0.10),
    "wallet": (0.015, 0.05),
}
GST_ON_FEE = 0.18

FAILURE_MODES = {
    "timing_lag_beyond_t2": 0.20,
    "fee_variance": 0.12,
    "duplicate_retry": 0.14,
    "partial_refund": 0.16,
    "missing_bank_reference": 0.12,
    "deemed_success_ambiguous": 0.08,
    "unexplained_shortage": 0.08,
    "held_for_risk_review": 0.06,
    "signature_verification_failed": 0.04,
    "duplicate_payment": 0.04,
}
EXCEPTION_RATE = 0.08
SPLIT_SETTLEMENT_GROUP_RATE = 0.10

# Single source of truth -- found via external review that this was
# previously a literal `20` duplicated at both generate_data.py's
# add_hard_negatives() call site and its dataset_metadata.json write,
# with nothing keeping the two in sync if one were ever edited alone.
HARD_NEGATIVE_PAIRS = 20

# Chargebacks are injected as their own transaction-id space AFTER the main
# payment generation (see data_generation/chargebacks.py), NOT added to
# FAILURE_MODES below -- adding a mode there would change the weights
# random.choices() draws from and reshuffle every existing payment's mode,
# invalidating the investigator benchmark, audit log, and every published
# number. This count is the single source of truth, same pattern as
# HARD_NEGATIVE_PAIRS above.
CHARGEBACK_COUNT = 14

# Razorpay Capital loan recoveries -- injected as their own transaction-id
# space (trn-loan###) for exactly the same RNG-stability reason as
# CHARGEBACK_COUNT above (see data_generation/loans.py).
LOAN_RECOVERY_COUNT = 18
# Recovery is contracted as a percentage of each settlement, which is how
# Razorpay Capital actually collects ("pay them as a percentage of your
# settlements"). Drawn per-loan from this range.
LOAN_RECOVERY_RATE_RANGE = (0.10, 0.30)
LOAN_PRINCIPAL_RANGE_RUPEES = (50_000, 500_000)

RISK_CLASS = {
    "clean": "none",
    "timing_lag_beyond_t2": "low",
    "fee_variance": "low",
    "duplicate_retry": "low",
    # A contracted, scheduled recovery that fully explains the shortage is
    # the same class of "explained variance" as fee_variance -- the money is
    # not missing, it was collected under an agreement the merchant signed.
    "loan_recovery_deduction": "low",
    "partial_refund": "medium",
    "missing_bank_reference": "medium",
    "deemed_success_ambiguous": "medium",
    "unexplained_shortage": "high",
    "held_for_risk_review": "high",
    "signature_verification_failed": "high",
    "duplicate_payment": "high",
    "hard_negative": "none",
    # chargebacks.py hardcodes "high" directly in its own ground-truth rows
    # (matching agent/policy_kb.py's POLICY-012 risk_class) rather than
    # looking this dict up -- chargeback_received is deliberately not in
    # config.FAILURE_MODES (see chargebacks.py's docstring), so it never
    # goes through ground_truth.py's config.RISK_CLASS.get(fm, "medium")
    # lookup path today. Added anyway: a future refactor that DID fold it
    # into that shared path would otherwise silently default to "medium"
    # with no error -- found by an external review pass, cheap to close
    # now while it's top of mind.
    "chargeback_received": "high",
}

AUTO_RESOLVABLE_MODES = {"timing_lag_beyond_t2", "fee_variance", "duplicate_retry",
                          "loan_recovery_deduction"}

INDIA_HOLIDAYS_2026 = {
    dt.date(2026, 8, 15), dt.date(2026, 10, 2), dt.date(2026, 10, 20),
    dt.date(2026, 11, 5), dt.date(2026, 1, 26),
}
