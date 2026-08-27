"""Configuration for the multi-partner bank ingestion / warehouse round-trip.

Razorpay settles through more than one banking partner in reality; this
dataset mimics that with two FICTIONAL partners (not modeled on any real
institution, deliberately) so each merchant's bank rows arrive in a
genuinely different raw export shape. Assignment is a static mapping, not
randomized -- this keeps it fully reproducible without consuming any of
data_generation's own seeded random draws (which everything from
payments.py through hard_negatives.py already depends on for the dataset's
known-good, previously-verified numbers).
"""

import random as _random
import string as _string

# Raw-export IDs (Txn_Ref_No/transactionId, orphan-credit UTRs) previously
# drew from data_generation.utils.rand_id()/rand_utr(), which use the
# SHARED global random stream every other generator module also depends on
# -- meaning adding/removing an ingestion transformation could silently
# perturb IDs generated later in the same process, even though those IDs
# never affect matching/evaluation (bank_txn_id is deliberately excluded
# from IDENTITY_COLUMNS in warehouse.py, and matching/ never compares
# reference IDs). Found via external review; fixed with a dedicated,
# independently-seeded RNG local to this package only -- canonical dataset
# generation (payments.py through hard_negatives.py) is untouched by this.
INGESTION_RNG_SEED = 4242
_rng = _random.Random(INGESTION_RNG_SEED)


def ingestion_rand_id(prefix: str, length: int = 12) -> str:
    chars = _string.ascii_letters + _string.digits
    return f"{prefix}_{''.join(_rng.choices(chars, k=length))}"


def ingestion_rand_utr() -> str:
    return f"{_rng.randint(100000000, 999999999)}{''.join(_rng.choices(_string.ascii_lowercase, k=6))}"


MERCHANT_PARTNER_ASSIGNMENT = {
    "merch_001": "suryaan",
    "merch_002": "suryaan",
    "merch_003": "suryaan",
    "merch_004": "northbridge",
    "merch_005": "northbridge",
}

PARTNER_DISPLAY_NAMES = {
    "suryaan": "Suryaan Bank",
    "northbridge": "Northbridge Bank",
}

# Orphan bank credits: genuine bank-side entries with no settlement they
# could ever match (an interest credit, a fee reversal from the bank
# itself) -- injected into this partner's raw export only. Amounts are
# chosen deliberately large (see ingestion/warehouse.py's identity/safety
# assertion, which checks this against the real dataset's actual settlement
# ceiling at generation time rather than trusting these numbers blindly).
ORPHAN_CREDIT_PARTNER = "northbridge"

ORPHAN_CREDITS = [
    {
        "bank_account_id": "acct_merch_004",
        "credit_amount_rupees": 18452300.50,
        "credit_date": "2026-07-18",
        "narration": "QUARTERLY ESCROW FLOAT INTEREST CREDIT",
    },
    {
        "bank_account_id": "acct_merch_004",
        "credit_amount_rupees": 9624100.75,
        "credit_date": "2026-07-25",
        "narration": "PROCESSING FEE REVERSAL - BANK ERROR CORRECTION",
    },
    {
        "bank_account_id": "acct_merch_005",
        "credit_amount_rupees": 21035400.15,
        "credit_date": "2026-07-11",
        "narration": "QUARTERLY ESCROW FLOAT INTEREST CREDIT",
    },
    {
        "bank_account_id": "acct_merch_005",
        "credit_amount_rupees": 7458900.20,
        "credit_date": "2026-07-29",
        "narration": "UNCLAIMED CHARGEBACK RESERVE RELEASE",
    },
]
