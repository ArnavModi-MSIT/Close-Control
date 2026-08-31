"""Shared contract for the partner connectors. Every connector converts
to/from exactly this canonical column set, matching data/bank_statement.csv's
existing 9-column schema unchanged.

Each connector implements four things:
    RAW_COLUMNS               its own raw export's field names
    to_raw(canonical_df)      canonical -> that partner's raw shape
    normalize(raw_df)         that partner's raw shape -> canonical
    write_raw / read_raw      how that raw shape is SERIALIZED on disk

The last pair exists because partners genuinely don't all ship CSV.
Suryaan sends CAMT.053 -- the ISO 20022 XML standard RBI is migrating
RTGS/NEFT reporting onto, and the format a real banking partner is most
likely to hand Razorpay -- while Northbridge sends a proprietary flat
export. Serialization therefore belongs to the connector, not to
warehouse.py, which now just asks the connector to write and read its own
bronze-layer file. Connectors that are happy with CSV delegate to the two
helpers below rather than reimplementing them.
"""

import os

import pandas as pd

CANONICAL_COLUMNS = [
    "bank_txn_id",
    "settlement_posting_id",
    "utr",
    "credit_amount_rupees",
    "credit_date",
    "value_date",
    "narration",
    "bank_account_id",
    "transaction_type",
]


def write_raw_csv(raw_df: pd.DataFrame, raw_dir: str, partner_name: str) -> str:
    """Default bronze-layer serializer: one CSV per partner."""
    path = os.path.join(raw_dir, f"{partner_name}.csv")
    raw_df.to_csv(path, index=False)
    return path


def read_raw_csv(path: str) -> pd.DataFrame:
    """Reads back what write_raw_csv wrote. keep_default_na=False so an
    empty field round-trips as "" rather than NaN -- the connectors' own
    normalize() decides what an absent value means, not pandas."""
    return pd.read_csv(path, keep_default_na=False)
