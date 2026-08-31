"""
Independently re-verifies the hash-chained `reviews` audit trail --
CLI entrypoint (same computation GET /api/audit-chain/verify exposes,
runnable without the server up).

    python scripts/verify_audit_chain.py
    python scripts/verify_audit_chain.py --tamper-test
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import argparse

from review_backend import db, chain


def _print_result(result: dict) -> None:
    print(f"Total review rows:     {result['total_rows']}")
    print(f"Pre-chain rows (NULL, written before chain_hash existed): {result['pre_chain_rows']}")
    print(f"Chain-verified rows:   {result['checked']}")
    if result["intact"]:
        print("CHAIN INTACT -- every row's stored hash matches its recomputed value.")
    else:
        b = result["broken_at"]
        print(f"CHAIN BROKEN at review id={b['id']} (review_uuid={b['review_uuid']}, "
              f"transaction_id={b['transaction_id']})")
        print(f"  expected: {b['expected']}")
        print(f"  stored:   {b['stored']}")


def _tamper_test() -> None:
    """Proves detection for real, not just describes the mechanism --
    same discipline as audit_manifest.py's own tamper test (append a byte
    to bank_statement.csv, confirm the hash changes, restore, confirm it's
    back). Picks one real review row, tampers its `notes` field in place,
    re-verifies (must report broken), restores the exact original value,
    re-verifies again (must report intact) -- so this also proves
    restoration is exact, not just that tampering is detected."""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT id, review_uuid, notes FROM reviews "
                            "WHERE chain_hash IS NOT NULL ORDER BY id ASC LIMIT 1").fetchone()
        if row is None:
            print("No chain-covered review rows exist yet -- nothing to tamper-test. "
                  "Submit at least one review through the API first.")
            return

        original_notes = row["notes"]
        print(f"Tampering review id={row['id']} ({row['review_uuid']})'s notes field...")
        conn.execute("UPDATE reviews SET notes = %s WHERE id = %s",
                      (f"{original_notes or ''} [TAMPERED]", row["id"]))
        conn.commit()

        tampered_result = chain.verify_chain(conn)
        print(f"After tampering: intact={tampered_result['intact']}"
              + (f", broken_at id={tampered_result['broken_at']['id']}"
                 if not tampered_result["intact"] else " (UNEXPECTED -- tamper not detected)"))

        print("Restoring original value...")
        conn.execute("UPDATE reviews SET notes = %s WHERE id = %s", (original_notes, row["id"]))
        conn.commit()

        restored_result = chain.verify_chain(conn)
        print(f"After restoring: intact={restored_result['intact']}"
              + (" (correctly back to intact)" if restored_result["intact"]
                 else " (UNEXPECTED -- restoration should have fixed this)"))

        if tampered_result["intact"] or not restored_result["intact"]:
            raise SystemExit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tamper-test", action="store_true",
                         help="Prove detection for real: tamper one row, verify it's caught, "
                              "restore it, verify it's clean again.")
    args = parser.parse_args()

    if args.tamper_test:
        _tamper_test()
    else:
        conn = db.get_connection()
        try:
            _print_result(chain.verify_chain(conn))
        finally:
            conn.close()
