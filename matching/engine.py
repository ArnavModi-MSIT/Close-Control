"""Core matching engine: settlement candidates (from gateway) -> bank
postings. Multi-pass, deterministic, no ML/LLM here -- this is the layer
that must be exactly right before anything probabilistic touches it.

Passes, in order, per settlement (processed in a stable, deterministic
order so consumption of bank rows is reproducible):
  1. Exact single-row match (within a few paise of rounding).
  2. Split match: two unconsumed rows summing to the expected total (1:N
     in comments/field names below, but _best_pair() only ever searches
     PAIRS -- the real supported relationship is a two-row split, not a
     general N-way one. Matcher and data generator agree on this, so it's
     internally consistent, just narrower than "1:N" implies; see
     CLAUDE.md's Known Limitations table. Flagged by an external review;
     documenting rather than renaming, since "1:N" is now the established
     name for this relationship across report.py/CLAUDE.md/the review
     -- queue app UI, and a rename would need to touch all of them for a
     labeling nuance, not a behavior change).
  3. Shortage-tolerant match: one row, plausibly short but not exact.
  4. Unmatched: no candidate found within the block at all.
"""

import pandas as pd

from . import config


def _best_single_candidate(candidates: pd.DataFrame, expected: float):
    """Return (row, abs_delta, is_tied) for the closest-amount unconsumed
    candidate, or (None, None, False) if none qualify at all."""
    if candidates.empty:
        return None, None, False
    deltas = (candidates["credit_amount_rupees"] - expected).abs()
    order = deltas.sort_values().index
    best_idx = order[0]
    best_delta = deltas.loc[best_idx]
    is_tied = False
    if len(order) > 1:
        second_delta = deltas.loc[order[1]]
        if best_delta <= config.EXACT_MATCH_TOLERANCE_RUPEES and second_delta <= config.EXACT_MATCH_TOLERANCE_RUPEES:
            # both candidates are essentially exact (including the 0-vs-0
            # case a relative-difference test can never catch) -- genuinely
            # indistinguishable, not just "close"
            is_tied = True
        elif best_delta > 0 and abs(second_delta - best_delta) / max(best_delta, 0.01) < config.AMBIGUITY_RELATIVE_DELTA:
            is_tied = True
    return candidates.loc[best_idx], best_delta, is_tied


def _best_pair(candidates: pd.DataFrame, expected: float):
    """Look for two unconsumed candidates whose sum is closest to expected.
    Also reports how many pairs are within exact tolerance, so callers can
    detect genuine pair ambiguity (multiple equally valid decompositions),
    not just return the single closest one blindly."""
    rows = list(candidates.itertuples())
    exact_pairs = []
    best = None
    best_delta = None
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            total = rows[i].credit_amount_rupees + rows[j].credit_amount_rupees
            delta = abs(total - expected)
            if delta <= config.EXACT_MATCH_TOLERANCE_RUPEES:
                exact_pairs.append((rows[i].Index, rows[j].Index))
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best = (rows[i].Index, rows[j].Index)
    if best is None:
        return None, None, 0
    return best, best_delta, len(exact_pairs)


def run_matching(settlements: pd.DataFrame, blocks: dict, bank: pd.DataFrame) -> pd.DataFrame:
    consumed = set()
    results = []

    ordered = settlements.sort_values(["settle_date", "settlement_id"])

    for _, s in ordered.iterrows():
        sid = s["settlement_id"]
        expected = s["expected_total_rupees"]
        block = blocks.get(sid, bank.iloc[0:0])
        available = block[~block["bank_txn_id"].isin(consumed)]

        result = {
            "settlement_id": sid,
            "merchant_id": s["merchant_id"],
            "member_count": s["member_count"],
            "expected_total_rupees": expected,
            "match_status": "unmatched",
            "match_pass": None,
            "matched_bank_txn_ids": [],
            "matched_utrs": [],
            "matched_total_rupees": None,
            "amount_delta_rupees": None,
            "confidence": "low",
            "had_ambiguous_candidates": False,
            "settlement_bank_relationship_observed": "unmatched",
            "missing_bank_reference": False,
            "bank_overage": False,
        }

        # --- Pass 1: exact single match ---
        exact_candidates = available[
            (available["credit_amount_rupees"] - expected).abs() <= config.EXACT_MATCH_TOLERANCE_RUPEES
        ]
        if not exact_candidates.empty:
            row, delta, tied = _best_single_candidate(exact_candidates, expected)

            if tied:
                # two+ candidates are essentially equally exact -- no
                # evidence distinguishes them, do not guess
                result.update({
                    "match_status": "ambiguous",
                    "match_pass": "multiple_exact_single_candidates",
                    "confidence": "low",
                    "had_ambiguous_candidates": True,
                    "settlement_bank_relationship_observed": "ambiguous",
                })
                results.append(result)
                continue

            # ambiguity-safety check (was missing before): before consuming
            # this single row, check whether a competing exact 2-row split
            # ALSO explains the total -- if so, both are structurally valid
            # and picking one arbitrarily would be wrong. Escalate instead.
            competing_split = False
            if len(available) >= 3:  # need >=2 other rows besides the single candidate
                other_rows = available[available["bank_txn_id"] != row["bank_txn_id"]]
                if len(other_rows) >= 2:
                    _, _, n_exact_pairs = _best_pair(other_rows, expected)
                    competing_split = n_exact_pairs > 0

            if competing_split:
                result.update({
                    "match_status": "ambiguous",
                    "match_pass": "exact_single_vs_split_conflict",
                    "confidence": "low",
                    "had_ambiguous_candidates": True,
                    "settlement_bank_relationship_observed": "ambiguous",
                })
                results.append(result)
                continue

            consumed.add(row["bank_txn_id"])
            result.update({
                "match_status": "matched",
                "match_pass": "exact",
                "matched_bank_txn_ids": [row["bank_txn_id"]],
                "matched_utrs": [row["utr"]],
                "matched_total_rupees": row["credit_amount_rupees"],
                "amount_delta_rupees": round(float(delta), 2),
                "confidence": "high" if not tied else "medium",
                "had_ambiguous_candidates": bool(tied),
                "settlement_bank_relationship_observed": "N:1" if s["member_count"] > 1 else "1:1",
                "missing_bank_reference": pd.isna(row["utr"]),
            })
            results.append(result)
            continue

        # --- Pass 2: split match (1:N) -- only worth trying if >=2 candidates present ---
        if len(available) >= 2:
            pair_idx, pair_delta, n_exact_pairs = _best_pair(available, expected)
            if pair_idx is not None and pair_delta <= config.EXACT_MATCH_TOLERANCE_RUPEES:
                if n_exact_pairs > 1:
                    # multiple valid decompositions -- genuinely ambiguous,
                    # do not arbitrarily pick one
                    result.update({
                        "match_status": "ambiguous",
                        "match_pass": "multiple_valid_splits",
                        "confidence": "low",
                        "had_ambiguous_candidates": True,
                        "settlement_bank_relationship_observed": "ambiguous",
                    })
                    results.append(result)
                    continue

                row_a = available.loc[pair_idx[0]]
                row_b = available.loc[pair_idx[1]]
                consumed.add(row_a["bank_txn_id"])
                consumed.add(row_b["bank_txn_id"])
                total = row_a["credit_amount_rupees"] + row_b["credit_amount_rupees"]
                result.update({
                    "match_status": "matched",
                    "match_pass": "split",
                    "matched_bank_txn_ids": [row_a["bank_txn_id"], row_b["bank_txn_id"]],
                    "matched_utrs": [row_a["utr"], row_b["utr"]],
                    "matched_total_rupees": round(float(total), 2),
                    "amount_delta_rupees": round(float(pair_delta), 2),
                    "confidence": "high",
                    "settlement_bank_relationship_observed": "1:N",
                    "missing_bank_reference": bool(pd.isna(row_a["utr"]) or pd.isna(row_b["utr"])),
                })
                results.append(result)
                continue

        # --- Pass 3: shortage-tolerant single match ---
        shortage_low = expected * config.SHORTAGE_TOLERANCE_MIN_FRACTION
        shortage_candidates = available[
            (available["credit_amount_rupees"] >= shortage_low) &
            (available["credit_amount_rupees"] < expected - config.EXACT_MATCH_TOLERANCE_RUPEES)
        ]
        if not shortage_candidates.empty:
            row, delta, tied = _best_single_candidate(shortage_candidates, expected)
            if tied:
                result.update({
                    "match_status": "ambiguous",
                    "match_pass": "ambiguous_shortage",
                    "confidence": "low",
                    "had_ambiguous_candidates": True,
                    "settlement_bank_relationship_observed": "ambiguous",
                })
                results.append(result)
                continue
            consumed.add(row["bank_txn_id"])
            result.update({
                "match_status": "matched_with_exception",
                "match_pass": "shortage_tolerant",
                "matched_bank_txn_ids": [row["bank_txn_id"]],
                "matched_utrs": [row["utr"]],
                "matched_total_rupees": row["credit_amount_rupees"],
                "amount_delta_rupees": round(float(delta), 2),
                "confidence": "medium",
                "had_ambiguous_candidates": bool(tied),
                "settlement_bank_relationship_observed": "N:1" if s["member_count"] > 1 else "1:1",
                "missing_bank_reference": pd.isna(row["utr"]),
            })
            results.append(result)
            continue

        # --- Pass 4: overage-tolerant single match (bank shows MORE than expected) ---
        overage_high = expected * config.OVERAGE_TOLERANCE_MAX_FRACTION
        overage_candidates = available[
            (available["credit_amount_rupees"] > expected + config.EXACT_MATCH_TOLERANCE_RUPEES) &
            (available["credit_amount_rupees"] <= overage_high)
        ]
        if not overage_candidates.empty:
            row, delta, tied = _best_single_candidate(overage_candidates, expected)
            if tied:
                result.update({
                    "match_status": "ambiguous",
                    "match_pass": "ambiguous_overage",
                    "confidence": "low",
                    "had_ambiguous_candidates": True,
                    "settlement_bank_relationship_observed": "ambiguous",
                })
                results.append(result)
                continue
            consumed.add(row["bank_txn_id"])
            result.update({
                "match_status": "matched_with_exception",
                "match_pass": "overage_tolerant",
                "matched_bank_txn_ids": [row["bank_txn_id"]],
                "matched_utrs": [row["utr"]],
                "matched_total_rupees": row["credit_amount_rupees"],
                "amount_delta_rupees": round(float(delta), 2),
                "confidence": "medium",
                "had_ambiguous_candidates": bool(tied),
                "settlement_bank_relationship_observed": "N:1" if s["member_count"] > 1 else "1:1",
                "missing_bank_reference": pd.isna(row["utr"]),
                "bank_overage": True,
            })
            results.append(result)
            continue

        # --- Pass 5: unmatched ---
        results.append(result)

    return pd.DataFrame(results)
