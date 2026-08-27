"""Cash position constants."""

import datetime as dt

# The dataset is entirely historical relative to real "now" (captures end
# 2026-07-30, all settlements conclude by ~2026-08-05). A forward forecast
# only means something if the snapshot date sits inside the batch window --
# this default was chosen by checking the actual in-transit/held counts at
# several candidate dates: 2026-07-25 gives 1525 already-settled, 188
# genuinely in-transit, 8 held -- a real forward pipeline, not just history.
DEFAULT_AS_OF = dt.date(2026, 7, 25)

# Observed max lag in the dataset is 5 business days (timing_lag_beyond_t2);
# 10 is a safe margin so the forecast horizon is never silently truncated
# for the default as-of date.
FORECAST_HORIZON_BUSINESS_DAYS = 10

# Reconciliation bridge tie-out tolerance. NOT a tiny absolute rupee amount:
# the documented residual on the curated dataset is real and explained
# (~0.13% -- shortage/overage-tolerance amounts inside a batched settlement
# that also has an unconfirmed member can't be attributed to one member
# transaction over another without inventing a rule), so a tight absolute
# tolerance would flag known-good state as untied. Tied if within ₹1 flat
# OR within 0.5% of the matched-confirmed population, whichever is larger --
# comfortably clears the known ~0.13% residual while still catching a
# genuinely broken bridge (e.g. a variance in the tens of percent).
RECONCILIATION_TIE_TOLERANCE_RUPEES = 1.00
RECONCILIATION_TIE_TOLERANCE_PCT = 0.005
