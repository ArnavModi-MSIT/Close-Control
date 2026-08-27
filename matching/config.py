"""Matching thresholds and tolerances."""

# blocking window
DATE_BLOCK_WINDOW_DAYS = 4      # candidate bank rows within +/- N days of settlement date
                                  # (must exceed 2, since a 1-business-day split offset
                                  # can span a weekend -> up to 3 calendar days)
AMOUNT_BLOCK_TOLERANCE_PCT = 0.5  # candidate bank rows within +/- 50% of settlement total (wide net; exact pass narrows it)

# exact match tolerance (paisa-level rounding only)
EXACT_MATCH_TOLERANCE_RUPEES = 0.02

# shortage-tolerant pass: accept a lower bank amount than expected, down to this fraction.
# generator's true shortage range is 92-98% of expected (2-8% shortfall) --
# 0.90 gives a small margin without being so permissive it accepts unrelated rows
SHORTAGE_TOLERANCE_MIN_FRACTION = 0.90

# overage-tolerant pass: accept a bank amount above expected, up to this fraction
OVERAGE_TOLERANCE_MAX_FRACTION = 1.15

# ambiguity: two+ candidates within this fraction of each other are indistinguishable
AMBIGUITY_RELATIVE_DELTA = 0.02  # 2%
