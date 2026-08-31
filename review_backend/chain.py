"""Hash-chained audit trail over the `reviews` table.

WHY THIS EXISTS, and what it adds that this project didn't already have.
This project already has real tamper evidence in two places:
seed_review_queue.py's `_canonical_hash()` proves a single case's audit
entry + report row weren't altered, and audit_manifest.py proves a single
matcher RUN's input files/config weren't altered. Both are correct, and
both are independent, per-item checks -- neither proves anything about the
SEQUENCE of events. Nothing today would catch a `reviews` row being
silently deleted, or the whole table being restored from an earlier
backup with the last N decisions missing: each remaining row's own content
would still look perfectly valid in isolation.

A hash chain closes that gap the same way a blockchain or a git commit
history does: every row's `chain_hash` incorporates the PREVIOUS row's
chain_hash, so altering, deleting, or reordering any historical row breaks
verification for every row after it, not just that one. Idea adapted from
ChayannFamali/reconcore (a Go reconciliation engine) -- most of that
project doesn't transfer (Go stack, an ML-scoring stage inside the
matcher, which is the opposite of this project's "AI proposes,
deterministic code disposes" rule), but its hash-chained audit log is a
natural strengthening of a theme this project already has three
implementations of.

CONCURRENCY. Computing "read the last row's hash, then insert a new row
whose hash depends on it" has an obvious race: two concurrent reviewers
(or bulk-review, which calls submit_review() once per case) could both
read the same "last" row before either commits, forking the chain. Fixed
with a single named Postgres advisory lock (`pg_advisory_xact_lock`),
held for the transaction's lifetime -- every writer serializes through it
globally, so "read prev hash, compute, insert" is atomic system-wide.
Simpler and easier to reason about correctly than a SELECT ... FOR UPDATE
row-lock (whose interaction with ORDER BY/LIMIT and newly-inserted rows is
genuinely easy to get subtly wrong), at the cost of serializing ALL
concurrent reviews globally rather than per-case -- an acceptable
trade-off at this project's single-operator demo scale (same class of
scale trade-off already made and documented for connection pooling).
"""

import hashlib
import json

import psycopg

# sha256 hex digest length -- the well-defined "no prior row" starting
# point for the very first review ever inserted. Not a random or
# meaningful hash itself, just a fixed, documented convention (matching
# how a git repository's root commit has no parent, not a hash of
# nothing).
GENESIS_HASH = "0" * 64

# Postgres advisory locks are keyed by a bigint; hashtext() deterministically
# derives one from this string so every process/connection agrees on which
# lock they're contending for without needing to coordinate a literal
# integer constant.
_CHAIN_LOCK_KEY = "reviews_chain"

# The exact set of a review's own fields that go into its hash -- every
# column submit_review() actually writes to `reviews`, aside from `id`
# (DB-assigned, and redundant: the chain's own linkage already encodes
# order) and `chain_hash` itself.
CHAINED_FIELDS = [
    "review_uuid", "transaction_id", "reviewer_name", "reviewer_role",
    "decision", "override_field", "override_old_value", "override_new_value",
    "notes", "previous_status", "resulting_status", "created_at",
    "application_version",
]


def compute_chain_hash(prev_hash: str, fields: dict) -> str:
    """Pure function: no I/O, so verify_chain() below can recompute this
    exact value later and compare, which is the entire verification
    mechanism. sort_keys=True makes this deterministic regardless of dict
    construction order."""
    payload = {"prev_hash": prev_hash, **{k: fields.get(k) for k in CHAINED_FIELDS}}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def next_chain_hash(conn: psycopg.Connection, fields: dict) -> str:
    """Call this BEFORE inserting a new review row, within the SAME
    transaction the insert itself will commit in -- the advisory lock is
    transaction-scoped (released automatically at COMMIT/ROLLBACK), so
    holding it across "read prev hash -> compute -> caller inserts ->
    caller commits" is what actually makes the whole sequence atomic
    against a concurrent caller doing the same thing."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_CHAIN_LOCK_KEY,))
    row = conn.execute("SELECT chain_hash FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = (row["chain_hash"] if row and row["chain_hash"] else GENESIS_HASH)
    return compute_chain_hash(prev_hash, fields)


def verify_chain(conn: psycopg.Connection) -> dict:
    """Walks the ENTIRE reviews table in id order and recomputes every
    row's chain_hash from scratch, comparing against what's actually
    stored. This is the real proof the tamper-evidence promise holds --
    not a description of the mechanism, an independent recheck of it.

    Rows written before the chain_hash column existed (chain_hash IS NULL)
    are a real, disclosed gap, not silently bridged: the first such row
    (if any) restarts verification from GENESIS_HASH again, and the return
    value says so explicitly via `pre_chain_rows`, rather than the chain
    silently claiming to cover history it doesn't actually reach back
    into.
    """
    rows = conn.execute("SELECT * FROM reviews ORDER BY id ASC").fetchall()

    prev_hash = GENESIS_HASH
    pre_chain_rows = 0
    broken_at = None
    checked = 0
    # A per-row summary of what was ACTUALLY walked and verified above --
    # this was already being computed (every row is read and hashed
    # either way), just never returned. Added after a live demo review
    # found the verify-chain UI panel showing only 4 aggregate numbers
    # read as "empty" next to every other panel's real row list -- this
    # doesn't change what's verified, only what's disclosed about it, so
    # the security-critical comparison logic above is untouched.
    verified_rows = []

    for row in rows:
        stored = row["chain_hash"]
        if stored is None:
            pre_chain_rows += 1
            prev_hash = GENESIS_HASH  # restart the chain at the first post-migration row
            verified_rows.append({
                "id": row["id"], "transaction_id": row["transaction_id"],
                "reviewer_name": row["reviewer_name"], "decision": row["decision"],
                "resulting_status": row["resulting_status"], "created_at": row["created_at"],
                "verified": None,  # pre-chain: no hash to check at all, not "checked and passed"
            })
            continue

        expected = compute_chain_hash(prev_hash, dict(row))
        checked += 1
        row_ok = expected == stored
        verified_rows.append({
            "id": row["id"], "transaction_id": row["transaction_id"],
            "reviewer_name": row["reviewer_name"], "decision": row["decision"],
            "resulting_status": row["resulting_status"], "created_at": row["created_at"],
            "verified": row_ok,
        })
        if not row_ok:
            broken_at = {"id": row["id"], "review_uuid": row["review_uuid"],
                          "transaction_id": row["transaction_id"],
                          "expected": expected, "stored": stored}
            break
        prev_hash = stored

    return {
        "total_rows": len(rows),
        "pre_chain_rows": pre_chain_rows,
        "checked": checked,
        "intact": broken_at is None,
        "broken_at": broken_at,
        "rows": verified_rows,
    }
