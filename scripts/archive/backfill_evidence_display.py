"""
One-time backfill: recompute `cases.evidence_fields_cited` for every
already-seeded case, using the fixed seed_review_queue._build_evidence_fields_cited().

Why this is needed: that helper used to look up a citation label like
"EVIDENCE-4" directly as a key in the matcher's report_row -- which never
matches, since report_row's real keys are "final_exception_type" etc., not
"EVIDENCE-N" labels. Every case seeded before the fix landed shows
"(not a known evidence field)" for EVERY citation in its "Evidence cited"
section, including genuinely valid ones (confirmed live: trn-000072 showed
all 6 of its real citations, including 2 real tool calls, as unknown).

Purely a display-projection fix -- `evidence_fields_cited` is derived from
(evidence_used, report_row, investigation_log), none of which are the
AI's frozen proposal content (agent_exception_type/agent_root_cause/etc.,
untouched here). Re-running this script is always safe: it's a pure
function of already-immutable inputs, so it converges to the same output
every time, never drifts, and only WRITEs a row whose recomputed value
actually differs from what's stored.

    python scripts/archive/backfill_evidence_display.py
    python scripts/archive/backfill_evidence_display.py --dry-run
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import os
import json
import argparse

from run_matcher import run
from review_backend import db
from seed_review_queue import (
    DATA_DIR, AUDIT_LOG_PATH, INVESTIGATION_LOG_PATH,
    _load_investigations, _primary_from_audit_entry, _primary_from_investigation,
    _build_evidence_fields_cited,
)


def backfill(data_dir: str = DATA_DIR, audit_log_path: str = AUDIT_LOG_PATH,
             investigation_log_path: str = INVESTIGATION_LOG_PATH, dry_run: bool = False) -> dict:
    report, _, _ = run(data_dir)
    report_by_txn = report.set_index("transaction_id").to_dict(orient="index")
    investigations = _load_investigations(investigation_log_path)

    with open(audit_log_path, encoding="utf-8") as f:
        audit_by_txn = {}
        for line in f:
            e = json.loads(line)
            audit_by_txn[e["transaction_id"]] = e  # latest wins, same as seed()'s own append-only assumption

    conn = db.get_connection()
    scanned, updated, skipped_no_source = 0, 0, []
    try:
        rows = conn.execute(
            "SELECT transaction_id, resolution_source, evidence_fields_cited FROM cases"
        ).fetchall()

        for row in rows:
            scanned += 1
            txn_id = row["transaction_id"]
            report_row = report_by_txn.get(txn_id)
            if report_row is None:
                skipped_no_source.append(txn_id)
                continue

            if row["resolution_source"] == "investigator":
                inv_entry = investigations.get(txn_id)
                if inv_entry is None:
                    skipped_no_source.append(txn_id)
                    continue
                primary = _primary_from_investigation(inv_entry, report_row)
                investigation_log = inv_entry.get("investigation_log")
            else:
                entry = audit_by_txn.get(txn_id)
                if entry is None:
                    skipped_no_source.append(txn_id)
                    continue
                primary = _primary_from_audit_entry(entry)
                investigation_log = None

            new_value = _build_evidence_fields_cited(primary["evidence_used"], report_row, txn_id, investigation_log)
            new_json = json.dumps(new_value, default=str)

            if new_json != row["evidence_fields_cited"]:
                updated += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE cases SET evidence_fields_cited=%s WHERE transaction_id=%s",
                        (new_json, txn_id),
                    )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {"scanned": scanned, "updated": updated, "skipped_no_source": skipped_no_source}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()

    result = backfill(dry_run=args.dry_run)
    print(f"Scanned: {result['scanned']} cases")
    print(f"{'Would update' if args.dry_run else 'Updated'}: {result['updated']} cases' evidence_fields_cited")
    if result["skipped_no_source"]:
        print(f"Skipped (no matching report row / audit / investigation entry found): "
              f"{len(result['skipped_no_source'])} -- {result['skipped_no_source'][:10]}")
