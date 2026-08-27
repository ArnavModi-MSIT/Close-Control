"""Internal settlement ledger generation: Razorpay's OWN general ledger --
independent expectations, never copied from the (possibly corrupted)
downstream gateway/processing record."""

import pandas as pd

from ..utils import add_business_days, rand_id, compute_fee_tax


def build_ledger_records(payments: pd.DataFrame):
    """This is Razorpay's own internal settlement ledger/GL -- what
    Razorpay itself expects to pay out, computed from its own MDR
    schedule, not read back from whatever the downstream gateway/
    processing pipeline actually did. That independence is what makes
    fee_variance and unexplained_shortage genuine, detectable
    discrepancies (an internal ledger disagreeing with a downstream
    execution system due to config drift or MDR-table staleness) rather
    than ledger corruption mirroring the exception -- a real internal-
    controls scenario, not just a relabeled merchant-facing file.

    Duplicate-payment children are excluded entirely: the internal ledger
    has no expectation for a charge nothing upstream told it was coming.
    """
    ledger_rows = []
    canonical = payments[~payments["is_duplicate_child"]]

    for _, p in canonical.iterrows():
        true_fee, true_tax = compute_fee_tax(p["gross_paise"], p["payment_method"], wrong_fee=False)
        gross_r = round(p["gross_paise"] / 100.0, 2)
        fee_r = round(true_fee / 100.0, 2)
        tax_r = round(true_tax / 100.0, 2)
        adj_r = 0.0  # ledger doesn't anticipate refunds/shortages in advance
        net_r = round(gross_r - fee_r - tax_r + adj_r, 2)

        expected_date = add_business_days(p["captured_at"].date(), 2)
        status = "pending" if p["failure_mode"] in ("timing_lag_beyond_t2", "held_for_risk_review") else "expected"

        ledger_rows.append({
            "ledger_id": rand_id("led", 10),
            "transaction_id": p["transaction_id"],
            "order_id": p["order_id"],
            "merchant_id": p["merchant_id"],
            "gross_amount_rupees": gross_r,
            "expected_fee_rupees": fee_r,
            "expected_tax_rupees": tax_r,
            "expected_adjustment_rupees": adj_r,
            "expected_net_settlement_rupees": net_r,
            "expected_settlement_date": expected_date.isoformat(),
            "status": status,
        })
    return pd.DataFrame(ledger_rows)
