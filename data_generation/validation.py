"""Dataset validation -- checks semantic correctness (dtypes, arithmetic,
uniqueness, leakage), not just structural existence. Fails loudly."""

import pandas as pd

from . import config
from .utils import add_business_days


def _validate_timing_lag_payments(payments, errors):
    """Anti-vacuity guard, same class as the loan-recovery/chargeback ones
    below: a timing_lag_beyond_t2 payment only demonstrates anything if it
    actually settles LATE.

    Found via a real bug, not written defensively in advance: payments.py's
    own `instant` draw (~5% of payments, independent of failure_mode except
    for an explicit held_for_risk_review exclusion) used to also override
    timing_lag_beyond_t2's intended 3-5 business-day lag, forcing
    settle_day = captured_day whenever both happened to co-occur. The
    payment then settles same-day while ground_truth.csv still labels it
    timing_lag_beyond_t2 -- a real label/data disagreement, caught by a
    real multi-seed accuracy sweep (scripts/run_seed_benchmark.py): 10 of
    25 independent seeds showed exactly this pattern, and the checked-in
    seed=42 dataset had one instance too (trn-001201, which happened to
    still score correctly only because it also independently carries an
    unrelated missing_bank_reference signal -- see CLAUDE.md's
    data_generation/ section). Fixed in payments.py; this guard exists so
    a future regression reports itself immediately instead of waiting for
    another multi-seed sweep to notice."""
    beyond_t2 = payments[(payments["failure_mode"] == "timing_lag_beyond_t2")
                          & (~payments["is_duplicate_child"])]
    if not len(beyond_t2):
        return

    captured_day = pd.to_datetime(beyond_t2["captured_at"]).dt.date
    standard_t2 = captured_day.apply(lambda d: add_business_days(d, 2))
    not_late = beyond_t2[beyond_t2["settle_day"] <= standard_t2]
    if len(not_late):
        errors.append(
            f"{len(not_late)} timing_lag_beyond_t2 payment(s) settle on or before the "
            f"standard T+2 date, so they are not actually late despite the label: "
            f"{not_late['transaction_id'].tolist()[:5]}"
        )


def _validate_loan_recoveries(gateway_df, ledger_df, loan_book_df, errors):
    """Anti-vacuity guard, same class as the hard-negative confusability
    check below: a loan recovery only demonstrates anything if it actually
    RECONCILES the shortfall it created.

    If the generator ever drifted so a recovery's amount stopped matching
    the gateway adjustment it represents, all of them would silently
    reclassify as unexplained_shortage. Every existing assertion would
    still pass, generation would still print happily, and the headline
    "18 recoveries auto-resolved, precision 1.0" would quietly be
    measuring nothing at all -- the exact failure mode this project's
    other anti-vacuity guards exist to catch.
    """
    if loan_book_df is None or not len(loan_book_df):
        return

    gw = gateway_df.set_index("transaction_id_ref")
    led = ledger_df.set_index("transaction_id")
    for _, rec in loan_book_df.iterrows():
        txn = rec["transaction_id"]
        if txn not in gw.index or txn not in led.index:
            errors.append(f"Loan recovery {rec['recovery_id']} references {txn}, "
                           f"which is missing from the gateway or ledger source.")
            continue
        recovery = round(float(rec["recovery_amount_rupees"]), 2)
        # The gateway books the recovery as a negative adjustment (in paise).
        adjustment = round(float(gw.loc[txn, "adjustment_paise"]) / 100.0, 2)
        if abs(adjustment + recovery) > 0.02:
            errors.append(f"Loan recovery {rec['recovery_id']} ({txn}): recovery "
                           f"Rs.{recovery:,.2f} does not match the gateway's booked "
                           f"adjustment Rs.{adjustment:,.2f} -- the recovery would no "
                           f"longer reconcile the shortfall it created.")
        # And the shortfall must be real: the ledger has to expect MORE than
        # the gateway settled, or there is nothing for the recovery to explain.
        expected_net = round(float(led.loc[txn, "expected_net_settlement_rupees"]), 2)
        observed_net = round(float(gw.loc[txn, "settlement_amount_paise"]) / 100.0, 2)
        if expected_net - observed_net <= 0:
            errors.append(f"Loan recovery {rec['recovery_id']} ({txn}) creates no shortfall "
                           f"(ledger expects Rs.{expected_net:,.2f}, gateway settled "
                           f"Rs.{observed_net:,.2f}) -- nothing for the recovery to explain, "
                           f"so this case proves nothing.")


def _validate_chargebacks(gateway_df, ledger_df, bank_df, gt_df, errors):
    """Anti-vacuity guard, same class as _validate_loan_recoveries() above:
    a chargeback only demonstrates anything if the clawback actually
    creates the "bank ties out with the post-clawback net, ledger still
    expects the pre-clawback net" gap chargeback_received exists to
    explain -- not just a row tagged with a chargeback_id.

    Found missing by an external review pass: matching/ledger_check.py
    classifies off chargeback_id presence, not off the arithmetic, so if
    the generator ever drifted (clawback zeroed, doubled, or no longer
    reconciling), every chargeback would still carry a chargeback_id and
    still classify as chargeback_received -- every existing check would
    keep passing, and the headline "14 chargebacks, precision/recall 1.0"
    would quietly stop meaning anything. Same blind spot
    _validate_loan_recoveries() already closes on the loan-recovery side.
    """
    cb_gw = gateway_df[gateway_df["chargeback_id"].notna()]
    if not len(cb_gw):
        return
    led = ledger_df.set_index("transaction_id")
    gt_by_txn = gt_df.set_index("transaction_id")
    bank_by_posting = bank_df.set_index("settlement_posting_id")["credit_amount_rupees"]
    for _, row in cb_gw.iterrows():
        txn = row["transaction_id_ref"]
        cb_id = row["chargeback_id"]
        if txn not in led.index or txn not in gt_by_txn.index:
            errors.append(f"Chargeback {cb_id} ({txn}) is missing from the ledger or ground truth.")
            continue
        posting_id = gt_by_txn.loc[txn, "settlement_posting_id"]
        if posting_id not in bank_by_posting.index:
            errors.append(f"Chargeback {cb_id} ({txn}): no bank posting found for "
                           f"settlement_posting_id {posting_id}.")
            continue
        bank_amount = round(float(bank_by_posting.loc[posting_id]), 2)
        gateway_net_after = round(float(row["settlement_amount_paise"]) / 100.0, 2)
        if abs(bank_amount - gateway_net_after) > 0.02:
            errors.append(f"Chargeback {cb_id} ({txn}): bank posting (Rs.{bank_amount:,.2f}) does "
                           f"not match the gateway's post-clawback net (Rs.{gateway_net_after:,.2f}) "
                           f"-- the settlement itself should still tie out exactly; only the "
                           f"ledger comparison should be off.")
        expected_net = round(float(led.loc[txn, "expected_net_settlement_rupees"]), 2)
        if expected_net - gateway_net_after <= 0:
            errors.append(f"Chargeback {cb_id} ({txn}) creates no shortfall (ledger expects "
                           f"Rs.{expected_net:,.2f}, gateway settled Rs.{gateway_net_after:,.2f}) "
                           f"-- nothing for chargeback_received to explain.")
        # net_original (== expected_net, booked before the dispute) + the
        # clawback adjustment must equal what the gateway actually settled
        # -- the fundamental arithmetic invariant, independent of whatever
        # discount factor chargebacks.py currently uses to compute it.
        adjustment = round(float(row["adjustment_paise"]) / 100.0, 2)
        if abs(expected_net + adjustment - gateway_net_after) > 0.02:
            errors.append(f"Chargeback {cb_id} ({txn}): expected net (Rs.{expected_net:,.2f}) + "
                           f"adjustment (Rs.{adjustment:,.2f}) != gateway's post-clawback net "
                           f"(Rs.{gateway_net_after:,.2f}) -- the clawback arithmetic doesn't add up.")


def validate_dataset(payments, gateway_df, bank_df, ledger_df, gt_df, hard_negative_pairs=None,
                      loan_book_df=None):
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

        # --- are the hard negatives still actually HARD? ---
        # Count + distinctness (above) say the pairs exist and didn't merge.
        # Neither says they're confusable. A "hard negative" only earns the
        # name if the two payments are genuinely indistinguishable from
        # merchant + amount + date evidence alone -- that's the whole reason
        # the matcher is allowed to escalate them instead of picking one.
        # If the generator ever drifted so a pair had different amounts or
        # different merchants, the pair would be trivially separable, the
        # headline "40/40 hard negatives handled" would be measuring nothing,
        # and every existing check here would still pass silently.
        hn_ids = set(hn_txn_ids)
        hn_gw = gateway_df[gateway_df["transaction_id_ref"].isin(hn_ids)
                            & (gateway_df["attempt_status"] == "success")]
        # trn-hn007-0 / trn-hn007-1 -> pair key trn-hn007
        pair_keys = hn_gw["transaction_id_ref"].str.rsplit("-", n=1).str[0]
        for pair_key, grp in hn_gw.groupby(pair_keys):
            if len(grp) != 2:
                errors.append(f"Hard-negative pair {pair_key} has {len(grp)} successful gateway "
                               f"row(s), expected exactly 2.")
                continue
            if grp["merchant_id"].nunique() != 1:
                errors.append(f"Hard-negative pair {pair_key} spans two merchants -- trivially "
                               f"separable by merchant, so not a hard negative at all.")
            if grp["payment_amount_paise"].nunique() != 1:
                errors.append(f"Hard-negative pair {pair_key} has two different amounts "
                               f"({sorted(grp['payment_amount_paise'].unique())}) -- trivially "
                               f"separable by amount, so not a hard negative at all.")
            # Same-day-and-close-together is what makes the date window
            # useless as a discriminator; generator draws 2-15 minutes apart.
            # captured_at is a unix int in the in-memory frame this function
            # is normally called with, but pandas parses it back as a
            # Timestamp when gateway.json is re-read from disk -- handle both
            # so this can't crash a caller that reloaded the dataset.
            spread = grp["captured_at"].max() - grp["captured_at"].min()
            spread_seconds = int(spread.total_seconds()) if hasattr(spread, "total_seconds") else int(spread)
            if spread_seconds > 24 * 3600:
                errors.append(f"Hard-negative pair {pair_key} is {spread_seconds / 3600:.1f}h "
                               f"apart -- far enough that the date window alone separates them.")

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

    _validate_loan_recoveries(gateway_df, ledger_df, loan_book_df, errors)
    _validate_chargebacks(gateway_df, ledger_df, bank_df, gt_df, errors)
    _validate_timing_lag_payments(payments, errors)

    # --- chargeback raw count invariant (added via external review, same
    # class as the hard-negative count check above) -- chargeback_received
    # is deliberately absent from config.FAILURE_MODES (see
    # chargebacks.py's docstring), so the "every configured failure mode
    # appears at least once" scenario-coverage check above never covers it
    # -- nothing else was asserting the raw count at all. ---
    chargeback_count = int((gt_df["failure_mode"] == "chargeback_received").sum())
    if chargeback_count != config.CHARGEBACK_COUNT:
        errors.append(f"Expected {config.CHARGEBACK_COUNT} chargeback_received rows, "
                       f"found {chargeback_count}.")

    if errors:
        raise AssertionError("Dataset validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return {
        "loan_recovery_row_count": int(len(loan_book_df)) if loan_book_df is not None else 0,
        "chargeback_row_count": chargeback_count,
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
