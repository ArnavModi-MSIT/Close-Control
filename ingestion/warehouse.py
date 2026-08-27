"""Multi-partner bank ingestion round-trip: canonical bank rows -> each
partner's own raw export format -> back to canonical. This is a REALISM
layer, not a correctness layer -- data_generation/sources/bank.py already
produces the correct canonical truth (amounts, dates, UTRs, posting IDs);
this module never changes what a settlement's bank posting IS, only what
raw shape it takes on the way through, mimicking Razorpay settling through
more than one banking partner in real life.

The one deliberate exception: a handful of orphan bank credits (see
ingestion/config.py's ORPHAN_CREDITS) are injected directly into one
partner's raw export. These have no canonical origin at all -- by design,
they exercise matching/engine.py's "unmatched" bank-side path, which
otherwise never fires on this dataset (see evaluate.py section 0b).

    from ingestion.warehouse import run_ingestion
    bank_df_final, example = run_ingestion(bank_df, gateway_df, OUT_DIR)
"""

import datetime as dt
import os

import pandas as pd

from matching.config import AMOUNT_BLOCK_TOLERANCE_PCT

from . import config
from .config import ingestion_rand_id, ingestion_rand_utr
from .connectors import CANONICAL_COLUMNS, PARTNERS
from .connectors import northbridge

IDENTITY_COLUMNS = ["utr", "credit_amount_rupees", "credit_date", "value_date",
                     "bank_account_id", "transaction_type"]


def _partner_for_account(bank_account_id: str) -> str:
    merchant_id = bank_account_id.replace("acct_", "", 1)
    if merchant_id not in config.MERCHANT_PARTNER_ASSIGNMENT:
        raise ValueError(
            f"Unknown bank account: {bank_account_id!r} -- no partner assignment for "
            f"merchant {merchant_id!r} in ingestion/config.py's MERCHANT_PARTNER_ASSIGNMENT. "
            f"The 'acct_<merchant_id>' format is a hidden contract this makes explicit "
            f"instead of a generic KeyError."
        )
    return config.MERCHANT_PARTNER_ASSIGNMENT[merchant_id]


def _build_orphan_raw_rows() -> pd.DataFrame:
    """Orphan credits are fabricated directly in the target partner's raw
    schema (never round-tripped from a canonical row, since none exists)."""
    rows = []
    for credit in config.ORPHAN_CREDITS:
        date_ddmmyyyy = dt.date.fromisoformat(credit["credit_date"]).strftime("%d/%m/%Y")
        rows.append({
            "transactionId": ingestion_rand_id("nbtxn", 12),
            "postingReference": ingestion_rand_id("post", 10),
            "utrNo": ingestion_rand_utr(),
            "amount": f"{credit['credit_amount_rupees']:.2f}",
            "valueDate": date_ddmmyyyy,
            "postingDate": date_ddmmyyyy,
            "remarks": credit["narration"],
            "accountNumber": credit["bank_account_id"],
            "crDrIndicator": "C",
        })
    return pd.DataFrame(rows, columns=northbridge.RAW_COLUMNS)


def _max_real_settlement_total_rupees(gateway_df: pd.DataFrame) -> float:
    """Exact ceiling (not a guess) on how large any real settlement's
    expected total can be, computed the same way
    matching/settlement_builder.py does -- so the orphan-credit safety
    check below reflects the real blocking condition
    (matching/blocking.py's amt_high = expected_total * 1.5), not a proxy.
    """
    successful = gateway_df[gateway_df["attempt_status"] == "success"]
    eligible = successful[successful["settlement_id"].notna()]
    totals = eligible.groupby("settlement_id")["settlement_amount_paise"].sum() / 100.0
    return float(totals.max()) if len(totals) else 0.0


def _assert_orphans_will_never_match(gateway_df: pd.DataFrame) -> None:
    max_total = _max_real_settlement_total_rupees(gateway_df)
    # matching/blocking.py's amt_high = expected_total_rupees * (1 +
    # AMOUNT_BLOCK_TOLERANCE_PCT) -- imported directly (not a hand-copied
    # `1.5` literal, following an external review that flagged the original
    # version as an implicit, unenforced dependency on matching/blocking.py's
    # internal formula: if AMOUNT_BLOCK_TOLERANCE_PCT ever changed there,
    # this check would silently stop reflecting the real blocking condition).
    # A further 2x safety multiplier on top so this holds even against a
    # settlement this exact check didn't happen to see (defensive, not just exact).
    safe_floor = max_total * (1 + AMOUNT_BLOCK_TOLERANCE_PCT) * 2
    for credit in config.ORPHAN_CREDITS:
        if credit["credit_amount_rupees"] <= safe_floor:
            raise AssertionError(
                f"Orphan credit amount {credit['credit_amount_rupees']} is not safely above "
                f"the real dataset's settlement ceiling ({max_total:.2f}, safe floor {safe_floor:.2f}) "
                f"-- it could accidentally get pulled into a real settlement's match block. "
                f"Raise the amount in ingestion/config.py's ORPHAN_CREDITS."
            )


def _assert_identity_preserved(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """The real safety net for this round-trip: every row that went IN
    must come back byte-identical on every field except bank_txn_id (which
    real partners are expected to reissue in their own numbering). Relying
    on evaluate.py's aggregate numbers alone would NOT catch a
    corrupted-but-plausible row, since the matcher tolerates amount/date
    variance within blocks and could silently accept a slightly-wrong
    value as a valid match.

    Still raises AssertionError on any failure -- never silently continues.
    On success, returns a structured summary (found via external review:
    an internal-only assertion has no auditable trace of what it actually
    checked once it passes; a UI ingestion-control card, or a human
    verifying the demo, has nothing to show beyond "no exception was
    raised"). This turns that into an explicit, inspectable result."""
    # settlement_posting_id is used as the round-trip identity key below --
    # confirmed unique in the real input before trusting it as an index
    # (found via external review: an unenforced assumption here could make
    # the index-based comparison silently misleading rather than erroring).
    if before["settlement_posting_id"].duplicated().any():
        raise AssertionError(
            "ingestion round-trip precondition violated: settlement_posting_id is not "
            "unique in the real canonical bank input -- the identity check below assumes "
            "it is (a split settlement's 1:N postings are still each their own row with "
            "their own posting_id, so this should never fire on this dataset's generator)."
        )

    before_idx = before.set_index("settlement_posting_id")
    after_known = after[after["settlement_posting_id"].isin(before_idx.index)]
    after_idx = after_known.set_index("settlement_posting_id")

    if len(after_idx) != len(before_idx):
        raise AssertionError(
            f"ingestion round-trip lost or duplicated rows: {len(before_idx)} real rows in, "
            f"{len(after_idx)} matched back out."
        )
    if after_idx.index.duplicated().any():
        raise AssertionError("ingestion round-trip produced duplicate settlement_posting_id values.")

    # bank_txn_id is deliberately EXCLUDED from IDENTITY_COLUMNS (each partner
    # reissues its own numbering), but that means nothing else was checking
    # it at all -- validate its own primary-key properties on the final
    # normalized output instead (found via external review).
    new_ids = after["bank_txn_id"]
    if new_ids.isna().any():
        raise AssertionError("ingestion round-trip produced null bank_txn_id value(s).")
    if new_ids.duplicated().any():
        raise AssertionError("ingestion round-trip produced duplicate bank_txn_id value(s).")

    after_idx = after_idx.loc[before_idx.index]
    for col in IDENTITY_COLUMNS:
        left = before_idx[col]
        right = after_idx[col]
        both_na = left.isna() & right.isna()
        mismatch = ~((left == right) | both_na)
        if mismatch.any():
            bad_ids = before_idx.index[mismatch].tolist()[:5]
            raise AssertionError(
                f"ingestion round-trip corrupted column '{col}' for settlement_posting_id(s): {bad_ids}"
            )

    return {
        "round_trip_ok": True,
        "rows_before": int(len(before_idx)),
        "rows_after": int(len(after_idx)),
        "identity_fields_checked": list(IDENTITY_COLUMNS),
        "bank_txn_id_non_null": True,
        "bank_txn_id_unique": True,
    }


def run_ingestion(bank_df: pd.DataFrame, gateway_df: pd.DataFrame, out_dir: str):
    """Splits bank_df by each row's merchant's assigned partner, denormalizes
    into that partner's raw export format, writes the raw files to
    data/warehouse/raw/ (a genuine bronze/audit layer), re-normalizes back
    to the canonical 9-column schema, and returns the unioned result plus
    one real raw-vs-normalized example row for the demo to narrate.

    Returns (bank_df_final, example, metrics). example is
    {"partner", "raw_row", "normalized_row"} or None if there were no real
    rows to sample (shouldn't happen on this dataset). metrics is a
    structured ingestion-control summary (partners_processed, raw_rows,
    normalized_rows, orphan_rows, rows_round_tripped, plus the identity
    check's own result) -- added following an external review noting that
    an internal-only assertion has no auditable trace once it passes; this
    is what a UI ingestion-control card, or a human verifying the demo,
    would actually want to see (see CLAUDE.md's ingestion/ section).
    """
    _assert_orphans_will_never_match(gateway_df)

    bank_df = bank_df.copy()
    bank_df["_partner"] = bank_df["bank_account_id"].map(_partner_for_account)

    raw_dir = os.path.join(out_dir, "warehouse", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    normalized_parts = []
    example = None
    raw_row_count = 0
    orphan_row_count = 0

    for partner_name, connector in PARTNERS.items():
        partner_bank = bank_df[bank_df["_partner"] == partner_name].drop(columns=["_partner"])
        raw = connector.to_raw(partner_bank)
        raw_row_count += len(raw)

        if partner_name == config.ORPHAN_CREDIT_PARTNER:
            orphans = _build_orphan_raw_rows()
            orphan_row_count += len(orphans)
            raw_row_count += len(orphans)
            raw = pd.concat([raw, orphans], ignore_index=True)

        raw.to_csv(os.path.join(raw_dir, f"{partner_name}.csv"), index=False)

        normalized = connector.normalize(raw)[CANONICAL_COLUMNS]
        normalized_parts.append(normalized)

        if example is None and len(raw) > 0:
            example = {
                "partner": config.PARTNER_DISPLAY_NAMES[partner_name],
                "raw_row": raw.iloc[0].to_dict(),
                "normalized_row": normalized.iloc[0].to_dict(),
            }

    bank_df_final = pd.concat(normalized_parts, ignore_index=True)[CANONICAL_COLUMNS]

    identity_check = _assert_identity_preserved(bank_df.drop(columns=["_partner"]), bank_df_final)

    metrics = {
        "partners_processed": list(PARTNERS.keys()),
        "raw_rows": raw_row_count,
        "normalized_rows": len(bank_df_final),
        "orphan_rows": orphan_row_count,
        "rows_round_tripped": identity_check["rows_after"],
        "identity_check": identity_check,
    }

    return bank_df_final, example, metrics
