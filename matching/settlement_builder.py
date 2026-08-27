"""Build settlement candidates from gateway records: one candidate per
settlement_id, with the total net amount that should show up at the bank.

This is deterministic and doesn't require matching anything -- gateway.json
already tells you which payments belong to which settlement_id. The hard
problem (handled in engine.py) is finding which bank posting(s) correspond
to each candidate.
"""

import pandas as pd


def build_settlement_candidates(gateway: pd.DataFrame) -> pd.DataFrame:
    successful = gateway[gateway["attempt_status"] == "success"]
    eligible = successful[successful["settlement_id"].notna()]

    # merchant_id=("merchant_id", "first") below is only safe because
    # settlement_id is assigned per (merchant_id, settle_day) at generation
    # time (data_generation/settlements.py) -- a settlement_id spanning two
    # merchants should be structurally impossible. Assert it rather than
    # silently trusting the invariant, since a malformed upstream source
    # would otherwise corrupt a settlement's expected_total_rupees with a
    # payment from the wrong merchant.
    merchant_counts = eligible.groupby("settlement_id")["merchant_id"].nunique()
    bad = merchant_counts[merchant_counts > 1]
    if len(bad):
        raise ValueError(
            f"settlement_id spans multiple merchants (data integrity violation): "
            f"{bad.index.tolist()}"
        )

    grouped = eligible.groupby("settlement_id").agg(
        merchant_id=("merchant_id", "first"),
        member_count=("razorpay_payment_id", "count"),
        expected_total_rupees=("settlement_amount_rupees", "sum"),
        settle_date=("settled_at", lambda s: pd.to_datetime(s).dt.date.max()),
        member_payment_ids=("razorpay_payment_id", list),
        member_transaction_ids=("transaction_id_ref", list),
    ).reset_index()

    grouped["expected_total_rupees"] = grouped["expected_total_rupees"].round(2)
    return grouped
