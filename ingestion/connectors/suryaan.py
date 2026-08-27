"""Suryaan Bank raw export format (fictional partner) -- CAPS_SNAKE_CASE
columns, DD-MM-YYYY dates, CR/DR transaction-type indicator. Genuinely
different shape from northbridge.py's format, not just relabeled columns.
"""

import datetime as dt

import pandas as pd

from ..config import ingestion_rand_id
from .base import CANONICAL_COLUMNS

RAW_COLUMNS = [
    "Txn_Ref_No", "Settlement_Ref", "UTR_Number", "Credit_Amt",
    "Value_Dt", "Credit_Dt", "Narration_Text", "Merchant_Acct_No", "Txn_Type",
]

_TYPE_TO_RAW = {"credit": "CR"}
_TYPE_FROM_RAW = {"CR": "credit"}


def _iso_to_ddmmyyyy(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d-%m-%Y")


def _ddmmyyyy_to_iso(s: str) -> str:
    return dt.datetime.strptime(s, "%d-%m-%Y").date().isoformat()


def to_raw(canonical_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in canonical_df.iterrows():
        rows.append({
            "Txn_Ref_No": ingestion_rand_id("srybnk", 12),
            "Settlement_Ref": r["settlement_posting_id"],
            "UTR_Number": "" if pd.isna(r["utr"]) else r["utr"],
            "Credit_Amt": f"{r['credit_amount_rupees']:.2f}",
            "Value_Dt": _iso_to_ddmmyyyy(r["value_date"]),
            "Credit_Dt": _iso_to_ddmmyyyy(r["credit_date"]),
            "Narration_Text": r["narration"],
            "Merchant_Acct_No": r["bank_account_id"],
            "Txn_Type": _TYPE_TO_RAW.get(r["transaction_type"], r["transaction_type"]),
        })
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def normalize(raw_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in raw_df.iterrows():
        utr = r["UTR_Number"]
        if r["Txn_Type"] not in _TYPE_FROM_RAW:
            raise ValueError(
                f"Suryaan connector: unsupported Txn_Type {r['Txn_Type']!r} for "
                f"Txn_Ref_No={r['Txn_Ref_No']!r} -- known values: {sorted(_TYPE_FROM_RAW)}. "
                f"Fails loudly rather than silently passing an unrecognized value through "
                f"to canonical data (found via external review)."
            )
        rows.append({
            "bank_txn_id": r["Txn_Ref_No"],
            "settlement_posting_id": r["Settlement_Ref"],
            "utr": None if (pd.isna(utr) or utr == "") else utr,
            "credit_amount_rupees": round(float(r["Credit_Amt"]), 2),
            "credit_date": _ddmmyyyy_to_iso(r["Credit_Dt"]),
            "value_date": _ddmmyyyy_to_iso(r["Value_Dt"]),
            "narration": r["Narration_Text"],
            "bank_account_id": r["Merchant_Acct_No"],
            "transaction_type": _TYPE_FROM_RAW[r["Txn_Type"]],
        })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
