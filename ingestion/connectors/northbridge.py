"""Northbridge Bank raw export format (fictional partner) -- camelCase
columns, DD/MM/YYYY dates, C/D transaction-type indicator. Genuinely
different shape from suryaan.py's format, not just relabeled columns.
"""

import datetime as dt

import pandas as pd

from ..config import ingestion_rand_id
from .base import CANONICAL_COLUMNS

RAW_COLUMNS = [
    "transactionId", "postingReference", "utrNo", "amount",
    "valueDate", "postingDate", "remarks", "accountNumber", "crDrIndicator",
]

_TYPE_TO_RAW = {"credit": "C"}
_TYPE_FROM_RAW = {"C": "credit"}


def _iso_to_ddslashmmyyyy(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _ddslashmmyyyy_to_iso(s: str) -> str:
    return dt.datetime.strptime(s, "%d/%m/%Y").date().isoformat()


def to_raw(canonical_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in canonical_df.iterrows():
        rows.append({
            "transactionId": ingestion_rand_id("nbtxn", 12),
            "postingReference": r["settlement_posting_id"],
            "utrNo": "" if pd.isna(r["utr"]) else r["utr"],
            "amount": f"{r['credit_amount_rupees']:.2f}",
            "valueDate": _iso_to_ddslashmmyyyy(r["value_date"]),
            "postingDate": _iso_to_ddslashmmyyyy(r["credit_date"]),
            "remarks": r["narration"],
            "accountNumber": r["bank_account_id"],
            "crDrIndicator": _TYPE_TO_RAW.get(r["transaction_type"], r["transaction_type"]),
        })
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def normalize(raw_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in raw_df.iterrows():
        utr = r["utrNo"]
        if r["crDrIndicator"] not in _TYPE_FROM_RAW:
            raise ValueError(
                f"Northbridge connector: unsupported crDrIndicator {r['crDrIndicator']!r} for "
                f"transactionId={r['transactionId']!r} -- known values: {sorted(_TYPE_FROM_RAW)}. "
                f"Fails loudly rather than silently passing an unrecognized value through "
                f"to canonical data (found via external review)."
            )
        rows.append({
            "bank_txn_id": r["transactionId"],
            "settlement_posting_id": r["postingReference"],
            "utr": None if (pd.isna(utr) or utr == "") else utr,
            "credit_amount_rupees": round(float(r["amount"]), 2),
            "credit_date": _ddslashmmyyyy_to_iso(r["postingDate"]),
            "value_date": _ddslashmmyyyy_to_iso(r["valueDate"]),
            "narration": r["remarks"],
            "bank_account_id": r["accountNumber"],
            "transaction_type": _TYPE_FROM_RAW[r["crDrIndicator"]],
        })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
