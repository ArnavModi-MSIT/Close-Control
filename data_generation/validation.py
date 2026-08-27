"""Dataset validation -- checks semantic correctness (dtypes, arithmetic,
uniqueness, leakage), not just structural existence. Fails loudly."""

import pandas as pd

from . import config


def validate_dataset(payments, gateway_df, bank_df, ledger_df, gt_df, hard_negative_pairs=None):
    errors = []

    # --- numeric dtype checks (catches corrupted-join bugs like stringified Series) ---
    ledger_numeric_cols = ["gross_amount_rupees", "expected_fee_rupees", "expected_tax_rupees",
                            "expected_adjustment_rupees", "expected_net_settlement_rupees"]
    for col in ledger_numeric_cols:
        if not pd.api.types.is_numeric_dtype(ledger_df[col]):
            errors.append(f"ledger.{col} is not numeric (dtype={ledger_df[col].dtype}) -- likely a corrupted join.")
    gateway_numeric_cols = ["payment_amount_paise", "fee_paise", "tax_paise", "adjustment_paise",
                             "settlement_amount_paise"]
    for col in gateway_numeric_cols:
        if not pd.api.types.is_numeric_dtype(gateway_df[col]):
            errors.append(f"gateway.{col} is not numeric (dtype={gateway_df[col].dtype}).")

    # --- no stray pandas repr strings anywhere ---
    for name, df in [("ledger", ledger_df), ("gateway", gateway_df), ("bank", bank_df)]:
        obj_cols = df.select_dtypes(include=["object", "string"]).columns
        for col in obj_cols:
            if df[col].astype(str).str.contains("dtype:", na=False).any():
                errors.append(f"{name}.{col} contains a stringified pandas object (leaked Series/DataFrame).")

    # --- primary key uniqueness ---
    if gateway_df["razorpay_payment_id"].duplicated().any():
        errors.append("Duplicate razorpay_payment_id values in gateway (should be unique).")
    if bank_df["bank_txn_id"].duplicated().any():
        errors.append("Duplicate bank_txn_id values in bank statement.")
    if ledger_df["ledger_id"].duplicated().any():
        errors.append("Duplicate ledger_id values in ledger.")
    non_null_utr = bank_df["utr"].dropna()
    if non_null_utr.duplicated().any():
        errors.append("Duplicate non-null UTR values in bank statement.")

    # --- held_for_risk_review must have no settlement/bank exposure ---
    held = payments[(payments["failure_mode"] == "held_for_risk_review") & (~payments["is_duplicate_child"])]
    if held["settlement_id"].notna().any():
        errors.append("Some held_for_risk_review payments have a settlement_id (should be None).")

    # --- ground truth completeness / no leakage ---
    if not set(gt_df["payment_index"]).issuperset(set(payments["payment_index"])):
        errors.append("Some payments are missing from ground truth.")
    leak_cols = {"failure_mode", "ambiguity_flag", "ambiguity_reason", "payment_bank_relationship",
                 "settlement_bank_relationship", "is_clean_match", "expected_auto_resolvable",
                 "risk_class", "expected_resolution"}
    for name, df in [("gateway", gateway_df), ("bank", bank_df), ("ledger", ledger_df)]:
        leaked = leak_cols.intersection(df.columns)
        if leaked:
            errors.append(f"Ground-truth columns leaked into {name}: {leaked}")

    # --- settlement amount consistency: sum(gateway net, successful attempts) == sum(bank postings) ---
    eligible = payments[payments["eligible_for_settlement"]].dropna(subset=["settlement_id"])
    gw_indexed = gateway_df[gateway_df["attempt_status"] == "success"].set_index(
        "payment_index_internal")["settlement_amount_paise"]
    for settlement_id, group in eligible.groupby("settlement_id"):
        gw_total = sum(gw_indexed.get(idx, 0) for idx in group["payment_index"])
        bank_total_rupees = bank_df[bank_df["settlement_posting_id"].isin(
            gt_df.loc[gt_df["settlement_id"] == settlement_id, "settlement_posting_id"].unique()
        )]["credit_amount_rupees"].sum()
        if abs(round(gw_total / 100.0, 2) - round(bank_total_rupees, 2)) > 0.02:
            errors.append(
                f"Settlement {settlement_id}: gateway net sum ({gw_total/100:.2f}) != "
                f"bank posting total ({bank_total_rupees:.2f})."
            )

    # --- relationship type sanity: N:1 and 1:N must both actually exist ---
    n1_count = (gt_df["payment_bank_relationship"] == "N:1").sum()
    one_n_count = (gt_df["settlement_bank_relationship"] == "1:N").sum()
    if n1_count == 0:
        errors.append("No N:1 payment_bank_relationship found -- settlement grouping failed.")
    if one_n_count == 0:
        errors.append("No 1:N settlement_bank_relationship found -- split-settlement logic failed.")

    # --- hard-negative count invariant (added via external review) ---
    hard_negative_count = int((gt_df["failure_mode"] == "hard_negative").sum())
    if hard_negative_pairs is not None:
        expected = hard_negative_pairs * 2
        if hard_negative_count != expected:
            errors.append(f"Expected {expected} hard-negative rows ({hard_negative_pairs} pairs), "
                           f"found {hard_negative_count}.")
        # every pair must remain two DISTINCT transaction_ids after all joins --
        # a pair silently collapsing to one row would defeat the whole point
        hn_txn_ids = gt_df.loc[gt_df["failure_mode"] == "hard_negative", "transaction_id"]
        if hn_txn_ids.duplicated().any():
            errors.append("A hard-negative pair collapsed to a duplicate transaction_id after joins.")

    # --- scenario coverage: every configured failure mode actually appears at
    # least once -- a missing scenario would otherwise look like a matcher
    # success (nothing to fail on) rather than a generator gap ---
    generated_modes = set(gt_df["failure_mode"].unique())
    missing_modes = set(config.FAILURE_MODES) - generated_modes
    if missing_modes:
        errors.append(f"Configured failure mode(s) never generated: {sorted(missing_modes)} "
                       f"-- low-probability weight, or a real generation bug.")

    # --- referential integrity: every reference actually resolves.
    # gt_df, not `payments`, is the authoritative transaction_id universe --
    # hard negatives are a deliberately separate ID space only ever merged
    # into gateway_df/ledger_df/gt_df (via generate_data.py's concat calls),
    # never back into `payments` itself, so `payments` alone would falsely
    # flag every hard-negative row as an orphan reference (confirmed for
    # real: this was the actual bug on the first version of this check). ---
    real_transaction_ids = set(gt_df["transaction_id"])
    orphan_gw_refs = set(gateway_df["transaction_id_ref"]) - real_transaction_ids
    if orphan_gw_refs:
        errors.append(f"{len(orphan_gw_refs)} gateway row(s) reference a transaction_id not in "
                       f"ground truth: {sorted(orphan_gw_refs)[:5]}{'...' if len(orphan_gw_refs) > 5 else ''}")
    orphan_ledger_refs = set(ledger_df["transaction_id"]) - real_transaction_ids
    if orphan_ledger_refs:
        errors.append(f"{len(orphan_ledger_refs)} ledger row(s) reference a transaction_id not in "
                       f"ground truth: {sorted(orphan_ledger_refs)[:5]}{'...' if len(orphan_ledger_refs) > 5 else ''}")
    real_razorpay_payment_ids = set(gateway_df["razorpay_payment_id"])
    dup_child_refs = gateway_df.loc[gateway_df["duplicate_of_payment_id"].notna(), "duplicate_of_payment_id"]
    bad_dup_refs = set(dup_child_refs) - real_razorpay_payment_ids
    if bad_dup_refs:
        errors.append(f"{len(bad_dup_refs)} duplicate_of_payment_id value(s) don't reference a real "
                       f"razorpay_payment_id: {sorted(bad_dup_refs)[:5]}")

    # --- global conservation, not just per-settlement: sum(successful gateway
    # settlement amounts, restricted to the SAME eligible population the bank
    # side is restricted to) == sum(bank postings actually tied to a
    # settlement). Both sides filtered via gt_df's settlement_posting_id
    # rather than re-deriving `eligible_for_settlement` semantics here --
    # rows like held_for_risk_review can carry a computed-but-never-real
    # settlement_amount_paise (same caveat cash_position/engine.py documents
    # for observed_net_rupees), so an unfiltered gateway sum would NOT
    # conserve against the bank side even on a fully correct dataset; this
    # is a genuinely independent top-line signal (would also catch a
    # settlement silently dropped from iteration entirely), not a
    # guaranteed restatement of the per-settlement check above. ---
    real_bank_rows = bank_df[bank_df["settlement_posting_id"].isin(gt_df["settlement_posting_id"].dropna())]
    gateway_eligible_refs = set(gt_df.loc[gt_df["settlement_posting_id"].notna(), "transaction_id"])
    global_gateway_rupees = round(
        gateway_df[(gateway_df["attempt_status"] == "success")
                   & (gateway_df["transaction_id_ref"].isin(gateway_eligible_refs))]
        ["settlement_amount_paise"].sum() / 100.0, 2)
    global_bank_rupees = round(real_bank_rows["credit_amount_rupees"].sum(), 2)
    if abs(global_gateway_rupees - global_bank_rupees) > 0.02 * max(1, len(real_bank_rows)):
        errors.append(f"Global conservation broken: gateway settlement total (Rs.{global_gateway_rupees:,.2f}) "
                       f"!= non-orphan bank posting total (Rs.{global_bank_rupees:,.2f}).")

    if errors:
        raise AssertionError("Dataset validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return {
        "n1_payment_count": int(n1_count),
        "one_n_settlement_payment_count": int(one_n_count),
        "unique_settlements": int(eligible["settlement_id"].nunique()),
        "unique_bank_postings": int(bank_df["settlement_posting_id"].nunique()),
        "hard_negative_row_count": hard_negative_count,
        "failure_modes_generated": len(generated_modes),
        "failure_modes_configured": len(config.FAILURE_MODES),
        "global_gateway_settlement_rupees": global_gateway_rupees,
        "global_bank_posting_rupees": global_bank_rupees,
    }
