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

RISK_CLASS = {
    "clean": "none",
    "timing_lag_beyond_t2": "low",
    "fee_variance": "low",
    "duplicate_retry": "low",
    "partial_refund": "medium",
    "missing_bank_reference": "medium",
    "deemed_success_ambiguous": "medium",
    "unexplained_shortage": "high",
    "held_for_risk_review": "high",
    "signature_verification_failed": "high",
    "duplicate_payment": "high",
    "hard_negative": "none",
}

AUTO_RESOLVABLE_MODES = {"timing_lag_beyond_t2", "fee_variance", "duplicate_retry"}

INDIA_HOLIDAYS_2026 = {
    dt.date(2026, 8, 15), dt.date(2026, 10, 2), dt.date(2026, 10, 20),
    dt.date(2026, 11, 5), dt.date(2026, 1, 26),
}
