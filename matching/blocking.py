"""Blocking: cut the settlement x bank comparison space down before running
any matching logic. Bank rows are bucketed by merchant account and a date
window around each settlement's expected date; only bank rows in a
settlement's block are ever compared against it.
"""

import datetime as dt

import pandas as pd

from . import config


def bank_account_for_merchant(merchant_id: str) -> str:
    return f"acct_{merchant_id}"


def build_blocks(settlements: pd.DataFrame, bank: pd.DataFrame) -> dict:
    """Returns {settlement_id: bank_df subset} -- the candidate bank rows
    for each settlement, before any amount/date scoring happens."""
    blocks = {}
    window = dt.timedelta(days=config.DATE_BLOCK_WINDOW_DAYS)

    # pre-index bank rows by account for speed
    bank_by_account = {acct: grp for acct, grp in bank.groupby("bank_account_id")}

    for _, s in settlements.iterrows():
        acct = bank_account_for_merchant(s["merchant_id"])
        candidates = bank_by_account.get(acct)
        if candidates is None or candidates.empty:
            blocks[s["settlement_id"]] = candidates.iloc[0:0] if candidates is not None else bank.iloc[0:0]
            continue

        low = s["settle_date"] - window
        high = s["settle_date"] + window
        date_mask = (candidates["credit_date"] >= low) & (candidates["credit_date"] <= high)

        amt_low = s["expected_total_rupees"] * (1 - config.AMOUNT_BLOCK_TOLERANCE_PCT)
        amt_high = s["expected_total_rupees"] * (1 + config.AMOUNT_BLOCK_TOLERANCE_PCT)
        # split tranches aren't necessarily even -- a tranche can be a small
        # fraction of the total, so the lower bound must stay generous and
        # only serves to filter out obviously unrelated large bank rows.
        # See config.py's SPLIT_TRANCHE_LOWER_BOUND_* comment for why this
        # applies to every settlement, not just split-eligible ones, and
        # what that's measured to cost in candidate-block overlap.
        amt_low = min(amt_low, s["expected_total_rupees"] * config.SPLIT_TRANCHE_LOWER_BOUND_FRACTION,
                       config.SPLIT_TRANCHE_LOWER_BOUND_FLOOR_RUPEES)
        amount_mask = (candidates["credit_amount_rupees"] >= amt_low) & (candidates["credit_amount_rupees"] <= amt_high)

        blocks[s["settlement_id"]] = candidates[date_mask & amount_mask]

    return blocks
