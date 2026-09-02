"""Matching thresholds and tolerances."""

# blocking window
DATE_BLOCK_WINDOW_DAYS = 4      # candidate bank rows within +/- N days of settlement date
                                  # (must exceed 2, since a 1-business-day split offset
                                  # can span a weekend -> up to 3 calendar days)
AMOUNT_BLOCK_TOLERANCE_PCT = 0.5  # candidate bank rows within +/- 50% of settlement total (wide net; exact pass narrows it)
# blocking.py overrides the lower bound down to whichever is smallest of the
# ±50% tolerance above, this fraction of the settlement total, or this flat
# rupee floor -- a split tranche can be a small fraction of the settlement's
# total, so the lower bound must stay generous for the ~10% of settlements
# that actually split (SPLIT_SETTLEMENT_GROUP_RATE, data_generation/config.py).
# Applied unconditionally to every settlement's block, not just split-eligible
# ones -- for any settlement with expected_total_rupees > Rs.20 (nearly all of
# them at this dataset's transaction sizes), the flat floor always wins,
# meaning amount-based block narrowing is effectively disabled for most
# settlements, not just splitting ones. Measured, not assumed: on the real
# curated dataset, verify_consumption_invariants() still confirms zero
# double-consumed bank rows and zero non-tolerance deltas despite this --
# a real, quantified widening of candidate pools (matching/diagnostics.py's
# candidate_block_stats(), see CLAUDE.md), proven safe rather than
# theoretically assumed safe. Named here (was two bare literals) following
# an external review pass; values unchanged.
SPLIT_TRANCHE_LOWER_BOUND_FRACTION = 0.05
SPLIT_TRANCHE_LOWER_BOUND_FLOOR_RUPEES = 1.0

# exact match tolerance (paisa-level rounding only)
EXACT_MATCH_TOLERANCE_RUPEES = 0.02

# fee_variance reconciliation tolerance: is fee_delta + net_delta close enough
# to zero to call the fee/tax difference the TRUE explanation for the net
# delta? data_generation/utils.py's compute_fee_tax() chains two round()
# calls (fee to the nearest paisa, then tax off the rounded fee), which can
# introduce at most ~0.01 rupees of compounding rounding noise -- comfortably
# inside EXACT_MATCH_TOLERANCE_RUPEES already. Set equal to it (not a
# separate, looser value) since a genuine fee_variance (MDR corrupted by
# 0.2%-0.6% of gross) produces a fee_delta on the order of several rupees,
# not fractions of one -- there is no real slack this needs beyond ordinary
# rounding. Was a bare, uncentralized 0.5 literal (25x looser than needed,
# every other tolerance in this file either centralized or a deliberately
# reasoned value) -- found via external review; auto_resolve_eligible=True
# for this exception type made the excess slack a genuine, if unexercised
# on the curated dataset, misclassification risk.
FEE_VARIANCE_RECONCILIATION_TOLERANCE_RUPEES = EXACT_MATCH_TOLERANCE_RUPEES

# shortage-tolerant pass: accept a lower bank amount than expected, down to this fraction.
# generator's true shortage range is 92-98% of expected (2-8% shortfall) --
# 0.90 gives a small margin without being so permissive it accepts unrelated rows
SHORTAGE_TOLERANCE_MIN_FRACTION = 0.90

# overage-tolerant pass: accept a bank amount above expected, up to this fraction
OVERAGE_TOLERANCE_MAX_FRACTION = 1.15

# ambiguity: two+ candidates within this fraction of each other are indistinguishable
AMBIGUITY_RELATIVE_DELTA = 0.02  # 2%

# Benford's Law first-digit test (matching/diagnostics.py): Nigrini's own
# guidance is to avoid this test on small samples -- a handful of rows can
# look "nonconforming" by pure chance with no real signal behind it. 100 is
# a commonly-cited practical floor for the first-digit (9-category) test;
# below it, benford_first_digit_analysis() skips the group entirely rather
# than reporting a verdict that isn't statistically meaningful.
BENFORD_MIN_SAMPLE_SIZE = 100
