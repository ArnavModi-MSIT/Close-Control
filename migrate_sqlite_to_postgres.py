"""One-time migration: copy the curated review-queue data from the legacy
SQLite file into a local Postgres database (see postgres/docker-compose.yaml).

Read-only against the source -- opens it via SQLite's read-only URI mode,
so this script structurally cannot write back to the original file. Refuses
to touch a target database that already has any rows in it, so re-running
this by accident can't duplicate or corrupt anything. Verifies row counts
AND full set-equality of transaction_id/review_uuid between source and
target before ever printing PASS -- a coincidental count match can't mask
wrong rows.

    python migrate_sqlite_to_postgres.py --target-database-url postgresql://review_app:review_app_local_dev@localhost:5433/review_queue_dryrun
    python migrate_sqlite_to_postgres.py --source data/review_queue.db --target-database-url ...
"""

import argparse
import os
import sqlite3
import sys

from review_backend import db as review_db

DEFAULT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "review_queue.db")


def _sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in sconn.execute(f"PRAGMA table_info({table})")]


def _pg_columns(pconn, table: str) -> set[str]:
    rows = pconn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
    ).fetchall()
    return {r["column_name"] for r in rows}


def _copy_table(sconn: sqlite3.Connection, pconn, table: str) -> int:
    """Explicit named columns on both the SELECT and the INSERT -- never
    SELECT * / positional-tuple copying, even though today's column sets
    match. This is the difference between a silent column-misalignment
    corruption and a loud, obvious error if they ever drift."""
    columns = _sqlite_columns(sconn, table)
    pg_cols = _pg_columns(pconn, table)
    missing = set(columns) - pg_cols
    if missing:
        print(f"ABORT: target table {table!r} is missing columns present in the source: {sorted(missing)}")
        print("DO NOT TRUST THIS DATABASE -- schema mismatch, nothing was copied for this table.")
        sys.exit(1)

    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    rows = sconn.execute(f"SELECT {col_list} FROM {table} ORDER BY id ASC").fetchall()
    for row in rows:
        pconn.execute(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            tuple(row[c] for c in columns),
        )
    return len(rows)


def _realign_sequence(pconn, table: str) -> None:
    """After inserting explicit id values (preserving the source's real
    ids, not minting fresh ones -- reviews.id is shown in review_history
    and _get_reviews orders by it), the table's own identity sequence is
    still at its start value. Point it past the highest id actually
    inserted so the next real INSERT doesn't collide."""
    pconn.execute(
        f"SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1))",
        (table,),
    )


def migrate(source_path: str, target_database_url: str) -> bool:
    print(f"Source (read-only): {source_path}")
    print(f"Target:             {target_database_url}")
    print()

    sconn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    sconn.row_factory = sqlite3.Row

    review_db.DATABASE_URL = target_database_url
    review_db.init_db()
    pconn = review_db.get_connection()

    try:
        src_case_count = sconn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        src_review_count = sconn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]

        existing_cases = pconn.execute("SELECT COUNT(*) AS c FROM cases").fetchone()["c"]
        existing_reviews = pconn.execute("SELECT COUNT(*) AS c FROM reviews").fetchone()["c"]
        if existing_cases or existing_reviews:
            print(f"ABORT: target already has {existing_cases} cases / {existing_reviews} reviews. "
                  f"Refusing to copy into a non-empty database -- point at a fresh one.")
            return False

        print(f"Copying cases ({src_case_count} source rows)...")
        n_cases = _copy_table(sconn, pconn, "cases")
        print(f"Copying reviews ({src_review_count} source rows)...")
        n_reviews = _copy_table(sconn, pconn, "reviews")

        _realign_sequence(pconn, "cases")
        _realign_sequence(pconn, "reviews")
        pconn.commit()

        src_txn_ids = {r["transaction_id"] for r in sconn.execute("SELECT transaction_id FROM cases")}
        tgt_txn_ids = {r["transaction_id"] for r in pconn.execute("SELECT transaction_id FROM cases")}
        src_review_uuids = {r["review_uuid"] for r in sconn.execute("SELECT review_uuid FROM reviews")}
        tgt_review_uuids = {r["review_uuid"] for r in pconn.execute("SELECT review_uuid FROM reviews")}

        ok = True
        print()
        print(f"cases:   source={src_case_count}  target={n_cases}  "
              f"{'OK' if src_case_count == n_cases else 'MISMATCH'}")
        print(f"reviews: source={src_review_count}  target={n_reviews}  "
              f"{'OK' if src_review_count == n_reviews else 'MISMATCH'}")
        if src_case_count != n_cases or src_review_count != n_reviews:
            ok = False
        if src_txn_ids != tgt_txn_ids:
            ok = False
            print(f"MISMATCH: transaction_id sets differ -- "
                  f"only in source: {sorted(src_txn_ids - tgt_txn_ids)[:5]}, "
                  f"only in target: {sorted(tgt_txn_ids - src_txn_ids)[:5]}")
        else:
            print("cases:   transaction_id set-equality  OK")
        if src_review_uuids != tgt_review_uuids:
            ok = False
            print("MISMATCH: review_uuid sets differ between source and target")
        else:
            print("reviews: review_uuid set-equality     OK")

        print()
        if ok:
            print("PASS -- migration verified row-for-row.")
        else:
            print("FAIL -- DO NOT TRUST THIS DATABASE. Investigate before using it for anything.")
        return ok
    finally:
        sconn.close()
        pconn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--target-database-url", required=True,
                         help="Postgres connection string for the TARGET database -- e.g. "
                              "postgresql://review_app:review_app_local_dev@localhost:5433/review_queue_dryrun")
    args = parser.parse_args()
    success = migrate(args.source, args.target_database_url)
    sys.exit(0 if success else 1)
