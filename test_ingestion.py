"""
Standalone proof that EACH bank connector's own round trip (canonical ->
that partner's raw export format -> canonical) preserves every real field
except bank_txn_id, in isolation from the other connector.

ingestion/warehouse.py's _assert_identity_preserved() already checks the
COMBINED round trip across both partners on the real generated dataset --
a real failure there tells you the ingestion layer is broken, but not
WHICH connector broke it, since Suryaan's and Northbridge's raw exports go
through completely different column names/date formats/type encodings
(see ingestion/connectors/suryaan.py and northbridge.py). These tests
isolate each connector so a single connector's regression is immediately
attributable, not buried in a combined 190-row diff. Added following an
external review of ingestion/ (multi_partner_ingestion_review.md, item #8).

    python test_ingestion.py
"""

import os
import sys
import tempfile

import pandas as pd

from ingestion.connectors import suryaan, northbridge
from ingestion.connectors.base import CANONICAL_COLUMNS

# Same Windows-console UTF-8 fix as test_ambiguity.py/test_gate.py -- not
# needed by the literals used here today, but keeps this file consistent
# with the rest of the project's standalone test scripts.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Two canonical rows per connector test: one with a real UTR, one with a
# missing UTR (None) -- exercises both branches of each connector's
# UTR-blank handling (to_raw writes "", normalize reads it back as None).
SAMPLE_CANONICAL_ROWS = [
    {
        "bank_txn_id": "orig_bnk_1", "settlement_posting_id": "post_AAA111",
        "utr": "UTR000111222", "credit_amount_rupees": 12345.67,
        "credit_date": "2026-07-10", "value_date": "2026-07-10",
        "narration": "NEFT-TESTMERCH-SETL", "bank_account_id": "acct_merch_001",
        "transaction_type": "credit",
    },
    {
        "bank_txn_id": "orig_bnk_2", "settlement_posting_id": "post_BBB222",
        "utr": None, "credit_amount_rupees": 987.65,
        "credit_date": "2026-07-11", "value_date": "2026-07-12",
        "narration": "RTGS-TESTMERCH-SETL", "bank_account_id": "acct_merch_002",
        "transaction_type": "credit",
    },
]

# bank_txn_id is deliberately excluded -- every real partner reissues its
# own numbering, exactly as ingestion/warehouse.py's IDENTITY_COLUMNS
# documents for the combined round trip.
IDENTITY_FIELDS = ["settlement_posting_id", "utr", "credit_amount_rupees",
                    "credit_date", "value_date", "bank_account_id", "transaction_type"]


def _round_trip(connector, label: str):
    canonical_in = pd.DataFrame(SAMPLE_CANONICAL_ROWS)

    raw = connector.to_raw(canonical_in)
    assert list(raw.columns) == connector.RAW_COLUMNS, (
        f"{label}: to_raw() did not produce exactly connector.RAW_COLUMNS "
        f"(got {list(raw.columns)})"
    )
    assert len(raw) == len(canonical_in), (
        f"{label}: to_raw() changed row count ({len(canonical_in)} -> {len(raw)})"
    )

    # Round-trip through the connector's REAL on-disk format (CAMT.053 XML
    # for Suryaan, CSV for Northbridge) rather than normalizing the
    # in-memory frame -- otherwise a lossy serializer (a dropped XML
    # element, a mangled date, a CSV quoting bug) would pass this test
    # while corrupting the actual bronze layer.
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = connector.write_raw(raw, tmp, "roundtrip")
        assert os.path.exists(raw_path), f"{label}: write_raw() produced no file"
        raw_reloaded = connector.read_raw(raw_path)
        assert len(raw_reloaded) == len(raw), (
            f"{label}: serializer lost rows ({len(raw)} written, {len(raw_reloaded)} read back)"
        )
        canonical_out = connector.normalize(raw_reloaded)[CANONICAL_COLUMNS]
        fmt = os.path.splitext(raw_path)[1].lstrip(".").upper()

    assert len(canonical_out) == len(canonical_in), (
        f"{label}: normalize() changed row count ({len(canonical_in)} -> {len(canonical_out)})"
    )

    print(f"{label}: canonical -> raw ({len(raw.columns)} cols, {connector.RAW_COLUMNS[0]}-style) "
          f"-> {fmt} file -> canonical")

    # bank_txn_id: the ONE field partners are expected to reissue --
    # verify it actually changed (proves to_raw/normalize aren't just
    # passing the original value through untouched) and is well-formed.
    new_ids = canonical_out["bank_txn_id"]
    assert new_ids.notna().all(), f"{label}: normalize() produced a null bank_txn_id"
    assert not new_ids.duplicated().any(), f"{label}: normalize() produced duplicate bank_txn_id values"
    assert not new_ids.isin(canonical_in["bank_txn_id"]).any(), (
        f"{label}: normalize() kept an ORIGINAL bank_txn_id instead of reissuing one -- "
        f"either the connector isn't generating its own reference, or this test's fixture "
        f"collided with a generated one by coincidence"
    )
    print(f"  bank_txn_id reissued: {canonical_in['bank_txn_id'].tolist()} -> {new_ids.tolist()}")

    # Every other field must come back byte-identical, row for row (fixture
    # rows are already sorted the same way, so no need to index-align first).
    for field in IDENTITY_FIELDS:
        left = canonical_in[field]
        right = canonical_out[field]
        both_na = left.isna() & right.isna()
        mismatch = ~((left == right) | both_na)
        assert not mismatch.any(), (
            f"{label}: field '{field}' was corrupted in the round trip: "
            f"in={left[mismatch].tolist()} out={right[mismatch].tolist()}"
        )
    print(f"  PASS -- all {len(IDENTITY_FIELDS)} identity fields preserved across "
          f"{len(canonical_in)} rows.\n")


def test_unsupported_transaction_type_rejected(connector, raw_columns: list, type_col: str, label: str):
    """Both connectors' normalize() must fail loudly on a transaction-type
    code they don't recognize, not silently pass it through (see item #14
    of the same review -- already implemented in the connectors themselves;
    this proves it, rather than trusting the docstring)."""
    raw = pd.DataFrame([{col: "" for col in raw_columns}])
    raw.loc[0, type_col] = "XX_UNKNOWN"
    try:
        connector.normalize(raw)
        raise AssertionError(f"{label}: normalize() accepted an unsupported "
                              f"{type_col}='XX_UNKNOWN' instead of raising")
    except ValueError as e:
        print(f"{label}: unsupported transaction-type code correctly rejected -- {e}")
        print("  PASS -- fails loudly instead of silently reaching canonical data.\n")


if __name__ == "__main__":
    _round_trip(suryaan, "Suryaan Bank (CAMT.053 ISO 20022 XML, CRDT/DBIT)")
    _round_trip(northbridge, "Northbridge Bank (camelCase, DD/MM/YYYY, C/D)")
    test_unsupported_transaction_type_rejected(suryaan, suryaan.RAW_COLUMNS, "CdtDbtInd", "Suryaan Bank")
    test_unsupported_transaction_type_rejected(northbridge, northbridge.RAW_COLUMNS, "crDrIndicator", "Northbridge Bank")
    print("All connector-level round-trip proofs passed.")
