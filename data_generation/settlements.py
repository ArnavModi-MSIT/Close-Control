"""Settlement group assignment: (merchant, settle_day) bucketing, split/missing-UTR decisions."""

import numpy as np
import pandas as pd

from . import config
from .utils import rand_id


def assign_settlement_groups(payments: pd.DataFrame) -> pd.DataFrame:
    """Group eligible payments by (merchant, settle_day). This naturally
    produces N:1 groups since many payments share a merchant+day."""
    eligible = payments[payments["eligible_for_settlement"]].copy()
    group_keys = eligible.groupby(["merchant_id", "settle_day"]).ngroup()
    eligible["settlement_group_key"] = group_keys

    settlement_id_map = {key: rand_id("setl", 12) for key in eligible["settlement_group_key"].unique()}
    eligible["settlement_id"] = eligible["settlement_group_key"].map(settlement_id_map)

    payments = payments.merge(
        eligible[["payment_index", "settlement_id", "settlement_group_key"]],
        on="payment_index", how="left"
    )
    return payments


def decide_group_properties(payments: pd.DataFrame):
    """For each settlement group, decide: does it split into two bank
    postings (1:N), and is its UTR missing (data-quality corruption)?"""
    groups = payments.dropna(subset=["settlement_id"])["settlement_id"].unique()
    rng = np.random.RandomState(config.RNG_SEED + 1)
    split_flags = {g: rng.random() < config.SPLIT_SETTLEMENT_GROUP_RATE for g in groups}

    missing_utr_groups = set(
        payments.loc[payments["failure_mode"] == "missing_bank_reference", "settlement_id"].dropna().unique()
    )
    return split_flags, missing_utr_groups
