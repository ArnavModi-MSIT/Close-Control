"""
One-time backfill: sanitize NaN/Infinity floats out of every JSON column
on already-seeded cases.

Why this is needed: a bank posting with no UTR at all is real, correct
data -- it's exactly what makes a missing_bank_reference case what it is --
but pandas represents that missing value as a raw float NaN, not None,
when pulled out of a Series (investigator/tools.py's get_settlement_details(),
matched_utrs). Python's own json.dumps() happily writes that as a
non-standard literal NaN token by default, so it flowed straight through
into investigation_log.jsonl and from there into Postgres untouched.
NaN is not valid JSON: Starlette's JSONResponse sets allow_nan=False and
correctly refuses to serialize it -- found live via a real 500 on
GET /api/cases/trn-000070, several layers downstream of where the NaN
was actually produced.

The root cause is fixed at TWO points, both upstream of this script:
  1. investigator/tools.py's get_settlement_details() now sanitizes
     matched_utrs at the source.
  2. investigator/loop.py's new json_safe() is applied at the JSONL
     append boundary in both run_investigator.py and run_demo.py's
     --live-case path, so ANY future tool result leaking a stray NaN
     (not just this one field) gets caught before it's ever written.

Neither fix touches data already written before they existed -- this
script is the one-time repair for that already-persisted state. Checked
at scale before deciding scope, not assumed from the one case that
surfaced it: 57 of 154 investigated cases in the live review queue (37%)
carry a NaN somewhere in investigation_log, and 1 in evidence_fields_cited
(a TOOL-N citation that copied the same value through) -- every JSON
column is scanned here, not just the one field the bug was first found in.

Purely a display-projection fix, same category as
backfill_evidence_display.py: sanitizing a NaN to None never changes what
a citation MEANS or which exception type/policy/confidence the case is
frozen at -- none of the AI's original proposal columns are touched here,
only the JSON blob columns that were failing to serialize at all. Safe to
re-run: converges to the same output every time, only writes a row whose
sanitized value actually differs from what's stored.

    python scripts/archive/backfill_json_sanitization.py
    python scripts/archive/backfill_json_sanitization.py --dry-run
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import json
import math
import argparse

from review_backend import db

JSON_COLUMNS = ["gate_reasons", "gate_condition_checks", "all_signals",
                 "evidence_fields_cited", "investigation_log"]


def _json_safe(obj):
    """Same logic as investigator/loop.py's json_safe() -- duplicated
    rather than imported so this repair script has zero dependency on
    investigator/ (a Postgres-only fix shouldn't require Ollama, torch,
    or any of investigator/'s own imports to even be installed)."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def backfill(dry_run: bool = False) -> dict:
    conn = db.get_connection()
    scanned = 0
    updated_by_column = {c: 0 for c in JSON_COLUMNS}
    updated_transaction_ids = set()
    try:
        select_cols = ", ".join(["transaction_id"] + JSON_COLUMNS)
        rows = conn.execute(f"SELECT {select_cols} FROM cases").fetchall()

        for row in rows:
            scanned += 1
            txn_id = row["transaction_id"]
            sets, params = [], []

            for col in JSON_COLUMNS:
                raw = row[col]
                if raw is None:
                    continue
                parsed = json.loads(raw)
                sanitized = _json_safe(parsed)
                new_json = json.dumps(sanitized, default=str)
                if new_json != raw:
                    sets.append(f"{col}=%s")
                    params.append(new_json)
                    updated_by_column[col] += 1

            if sets:
                updated_transaction_ids.add(txn_id)
                if not dry_run:
                    params.append(txn_id)
                    conn.execute(f"UPDATE cases SET {', '.join(sets)} WHERE transaction_id=%s", params)

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {
        "scanned": scanned,
        "updated_cases": len(updated_transaction_ids),
        "updated_by_column": {k: v for k, v in updated_by_column.items() if v},
        "updated_transaction_ids": sorted(updated_transaction_ids),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()

    result = backfill(dry_run=args.dry_run)
    print(f"Scanned: {result['scanned']} cases")
    print(f"{'Would update' if args.dry_run else 'Updated'}: {result['updated_cases']} cases")
    print(f"By column: {result['updated_by_column']}")
    if result["updated_transaction_ids"]:
        print(f"Transaction IDs: {result['updated_transaction_ids'][:15]}"
              + (" ..." if len(result["updated_transaction_ids"]) > 15 else ""))
