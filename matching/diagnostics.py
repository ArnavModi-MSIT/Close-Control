"""Observational diagnostics over the matcher's own output -- never used to
make a matching decision, never imported by engine.py/blocking.py/report.py
themselves. Answers questions an auditor or judge would ask ("why was this
transaction difficult," "how much does processing order actually matter
here," "did every bank rupee get accounted for") without having to read the
matching engine's code. Added following an external review of matching/
(deterministic_matching_review.md) that flagged candidate-overlap
visibility and consumption/conservation invariants as the highest-value
missing diagnostics -- the matcher's own greedy, order-dependent
consumption (see engine.py's docstring and CLAUDE.md's Known Limitations)
is a documented, accepted tradeoff, but until now nothing measured whether
it's actually a live risk on THIS dataset or a theoretical one.

    from matching.diagnostics import candidate_block_stats, verify_consumption_invariants, settlement_conservation_summary
"""

import pandas as pd

from . import config


def candidate_block_stats(blocks: dict, bank: pd.DataFrame) -> dict:
    """Per-settlement candidate block sizes, plus how many distinct blocks
    each bank row appears in BEFORE any consumption happens. This is the
    direct answer to "if bank rows are mostly disjoint across blocks,
    greedy consumption order can't matter; if many appear in 2+ blocks,
    that's real evidence a stronger global-assignment strategy would help"
    -- measured, not assumed."""
    sizes = pd.Series({sid: len(df) for sid, df in blocks.items()}, dtype="int64")

    membership: dict = {}
    for df in blocks.values():
        for txn_id in df["bank_txn_id"]:
            membership[txn_id] = membership.get(txn_id, 0) + 1
    membership_counts = pd.Series(membership, dtype="int64") if membership else pd.Series(dtype="int64")

    return {
        "settlement_block_count": len(blocks),
        "mean_block_size": round(float(sizes.mean()), 2) if len(sizes) else 0.0,
        "p95_block_size": float(sizes.quantile(0.95)) if len(sizes) else 0.0,
        "max_block_size": int(sizes.max()) if len(sizes) else 0,
        "empty_block_count": int((sizes == 0).sum()),
        "bank_rows_total": int(len(bank)),
        "bank_rows_appearing_in_any_block": int(len(membership_counts)),
        "bank_rows_in_zero_blocks": int(len(bank) - len(membership_counts)),
        "bank_rows_in_exactly_one_block": int((membership_counts == 1).sum()),
        "bank_rows_in_two_blocks": int((membership_counts == 2).sum()),
        "bank_rows_in_three_plus_blocks": int((membership_counts >= 3).sum()),
        "max_blocks_containing_one_bank_row": int(membership_counts.max()) if len(membership_counts) else 0,
    }


def verify_consumption_invariants(settlement_matches: pd.DataFrame, bank: pd.DataFrame) -> dict:
    """Global safety net over engine.py's own `consumed` set logic -- PROVES
    (raises AssertionError if violated, same fail-loud pattern as
    ingestion/warehouse.py's identity checks) that no bank_txn_id was ever
    claimed by more than one settlement's match, and that every claimed
    bank_txn_id genuinely exists in the real bank statement. Returns a
    structured summary on success rather than only asserting silently, so
    this is an auditable control result, not just an internal check nobody
    can see passed."""
    matched_lists = settlement_matches["matched_bank_txn_ids"]
    all_matched_ids = [txn_id for ids in matched_lists for txn_id in ids]

    matched_series = pd.Series(all_matched_ids, dtype="object")
    dup_consumed = matched_series[matched_series.duplicated()].unique().tolist()
    if dup_consumed:
        raise AssertionError(
            f"matching invariant violated: bank_txn_id(s) claimed by more than one "
            f"settlement's match: {dup_consumed[:5]}"
        )

    valid_bank_ids = set(bank["bank_txn_id"])
    phantom_ids = set(all_matched_ids) - valid_bank_ids
    if phantom_ids:
        raise AssertionError(
            f"matching invariant violated: matched bank_txn_id(s) that don't exist in "
            f"the real bank statement: {list(phantom_ids)[:5]}"
        )

    matched_count = len(set(all_matched_ids))
    return {
        "consumption_invariant_ok": True,
        "no_bank_row_double_consumed": True,
        "no_phantom_matched_ids": True,
        "bank_rows_total": len(valid_bank_ids),
        "bank_rows_matched": matched_count,
        "bank_rows_unmatched": len(valid_bank_ids) - matched_count,
    }


def settlement_conservation_summary(settlement_matches: pd.DataFrame) -> dict:
    """For every settlement the engine actually matched (exact/split/
    shortage/overage), classify matched_total vs. expected_total as
    within-tolerance / shortage / overage -- a financial audit trail over
    numbers engine.py already computed, not a new calculation. An exact
    or split match with a real (not just paisa-rounding) delta here would
    mean the engine's own pass logic and this summary disagree about what
    "exact" means -- exactly the kind of thing a conservation check is for."""
    matched = settlement_matches[settlement_matches["match_status"].isin(["matched", "matched_with_exception"])]
    deltas = matched["matched_total_rupees"] - matched["expected_total_rupees"]

    within_tolerance = int((deltas.abs() <= config.EXACT_MATCH_TOLERANCE_RUPEES).sum())
    shortage = int((deltas < -config.EXACT_MATCH_TOLERANCE_RUPEES).sum())
    overage = int((deltas > config.EXACT_MATCH_TOLERANCE_RUPEES).sum())

    exact_or_split_mismatch = matched[
        matched["match_pass"].isin(["exact", "split"]) & (deltas.abs() > config.EXACT_MATCH_TOLERANCE_RUPEES)
    ]["settlement_id"].tolist()

    return {
        "matched_settlements": int(len(matched)),
        "within_tolerance": within_tolerance,
        "shortage": shortage,
        "overage": overage,
        "exact_or_split_pass_with_real_delta": exact_or_split_mismatch,
        "total_expected_rupees": round(float(matched["expected_total_rupees"].sum()), 2),
        "total_matched_rupees": round(float(matched["matched_total_rupees"].sum()), 2),
    }
