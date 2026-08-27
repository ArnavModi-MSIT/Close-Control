"""
End-to-end tests for the review-queue API and its approval state machine.

Every transition rule in review_backend/state_machine.py was originally
verified by hand with curl. That proved the design worked once; it didn't
protect it from the next refactor. This does -- same rules, exercised over
real HTTP (real routing, real status codes, real Postgres writes), runnable
in about a second.

SAFETY: this never touches the real review_queue database. It creates its
own throwaway Postgres database (review_queue_test_<pid>) on the same local
server before the app starts, points review_backend.db.DATABASE_URL at it,
seeds two synthetic cases, and drops the database at the end. The demo
database (603 real cases plus hand-walked review history) is untouched.

Style matches test_gate.py / test_ambiguity.py -- plain asserts, no pytest,
runs as a script.

    python test_review_api.py
"""

import os
import sys
import json
import datetime as dt

import pandas as pd
import psycopg
from psycopg import sql

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Redirect the database BEFORE importing the app: review_backend.main calls
# db.init_db() at startup, and db.get_connection() reads DATABASE_URL at
# call time, so patching the module global here is enough to isolate
# everything that follows.
from review_backend import db as _db

_TEST_DB_NAME = f"review_queue_test_{os.getpid()}"

# CREATE DATABASE / DROP DATABASE cannot run inside a transaction block in
# Postgres, and can't target the database the connection is currently on --
# this needs its own connection, pointed at Postgres's always-present
# "postgres" maintenance database, with autocommit=True (unlike every other
# connection in this codebase, which relies on explicit conn.commit()).
_base_info = psycopg.conninfo.conninfo_to_dict(_db.DATABASE_URL)
_MAINTENANCE_URL = psycopg.conninfo.make_conninfo(**{**_base_info, "dbname": "postgres"})
_TEST_DATABASE_URL = psycopg.conninfo.make_conninfo(**{**_base_info, "dbname": _TEST_DB_NAME})

_maint_conn = psycopg.connect(_MAINTENANCE_URL, autocommit=True)
_maint_conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_TEST_DB_NAME)))

_db.DATABASE_URL = _TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402
import review_backend.main as _main  # noqa: E402
from review_backend.main import app  # noqa: E402

TIER1_TXN = "trn-test-tier1"   # small amount -> one analyst closes it
TIER2_TXN = "trn-test-tier2"   # >= MANAGER_APPROVAL_THRESHOLD_RUPEES -> needs two people

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")
        if detail:
            print(f"        {detail}")


def insert_case(conn, transaction_id: str, amount: float, tier: int,
                 gate_final_decision: str = "escalate",
                 investigation_gate_decision: str | None = None) -> None:
    """Minimal synthetic case. Only the columns the API actually reads for
    these tests need real values; the rest carry schema-valid placeholders."""
    conn.execute(
        """INSERT INTO cases (
            transaction_id, merchant_id, settlement_id, matcher_exception_type,
            agent_exception_type, reclassified, agent_root_cause,
            agent_recommended_action, agent_confidence, agent_policy_id,
            policy_id_consistent, agent_sufficient_evidence, gate_final_decision,
            gate_reasons, amount_at_risk_rupees, required_approval_tier,
            match_status, match_pass, risk_class, ledger_expected_net_rupees,
            observed_net_rupees, net_delta_rupees, all_signals,
            evidence_fields_cited, provider, model, agent_run_id, seeded_at,
            audit_log_source, audit_record_hash, schema_version,
            investigated, investigation_gate_decision
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            transaction_id, "merch_test", "setl_test", "missing_bank_reference",
            "missing_bank_reference", 0, "synthetic test case",
            "escalate", 0.5, "POLICY-004",
            1, 0, gate_final_decision,
            json.dumps(["test"]), amount, tier,
            "no_settlement", None, "high", amount,
            amount, 0.0, json.dumps(["missing_bank_reference"]),
            json.dumps([]), "test", "test", "test-run",
            dt.datetime.now(dt.timezone.utc).isoformat(),
            "test", f"hash-{transaction_id}", "1.0.0",
            int(investigation_gate_decision is not None), investigation_gate_decision,
        ),
    )


def setup() -> None:
    _db.init_db()
    conn = _db.get_connection()
    try:
        insert_case(conn, TIER1_TXN, 1000.0, 1)
        insert_case(conn, TIER2_TXN, 75000.0, 2)
        conn.commit()
    finally:
        conn.close()


def review(client, txn: str, **payload):
    return client.post(f"/api/cases/{txn}/review", json=payload)


def main() -> None:
    setup()
    client = TestClient(app)

    print("=" * 70)
    print("REVIEW QUEUE API -- state machine over real HTTP")
    print("=" * 70)
    print(f"Isolated database: {_TEST_DATABASE_URL}")
    print()

    # ---------------------------------------------------------------- reads
    print("Case reads")
    r = client.get(f"/api/cases/{TIER1_TXN}")
    check("GET a seeded case returns 200", r.status_code == 200, r.text[:200])
    body = r.json()
    check("new case derives status 'pending'",
          body["review_state"]["status"] == "pending", str(body["review_state"]))
    check("un-investigated case exposes investigation: null",
          body["investigation"] is None, str(body["investigation"]))

    r = client.get("/api/cases/trn-does-not-exist")
    check("GET an unknown case returns 404", r.status_code == 404, r.text[:200])
    print()

    # ------------------------------------------------------- tier 1 approve
    print("Tier 1 -- a single analyst closes the case")
    r = review(client, TIER1_TXN, reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="looks right")
    check("analyst approval accepted", r.status_code == 200, r.text[:200])
    check("tier-1 approval lands directly on 'approved'",
          r.status_code == 200 and r.json()["new_status"] == "approved", r.text[:200])

    r = review(client, TIER1_TXN, reviewer_name="ana2", reviewer_role="analyst",
               decision="approved", notes="again")
    check("re-approving a closed case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, TIER1_TXN, reviewer_name="ana2", reviewer_role="analyst",
               decision="overridden", override_field="agent_policy_id",
               override_old_value="POLICY-004", override_new_value="POLICY-007",
               notes="too late")
    check("overriding a closed case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")
    print()

    # ------------------------------------------------------- tier 2 approve
    print("Tier 2 -- analyst first, then a DIFFERENT manager")
    r = review(client, TIER2_TXN, reviewer_name="mgr", reviewer_role="manager",
               decision="approved", notes="skipping the analyst")
    check("manager-first on a tier-2 case is rejected with 422",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, TIER2_TXN, reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="analyst sign-off")
    check("analyst approval moves tier-2 to 'pending_manager_approval'",
          r.status_code == 200 and r.json()["new_status"] == "pending_manager_approval",
          r.text[:200])

    r = review(client, TIER2_TXN, reviewer_name="ana", reviewer_role="manager",
               decision="approved", notes="same person, different hat")
    check("same person cannot supply both tier-2 approvals (422)",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, TIER2_TXN, reviewer_name="ana2", reviewer_role="analyst",
               decision="approved", notes="second analyst")
    check("a second analyst cannot substitute for the manager (422)",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, TIER2_TXN, reviewer_name="mgr", reviewer_role="manager",
               decision="approved", notes="manager sign-off")
    check("a different manager closes the tier-2 case",
          r.status_code == 200 and r.json()["new_status"] == "approved", r.text[:200])
    print()

    # ---------------------------------------------------------- validation
    print("Payload validation")
    conn = _db.get_connection()
    try:
        insert_case(conn, "trn-test-ovr", 900.0, 1)
        insert_case(conn, "trn-test-esc", 900.0, 1)
        conn.commit()
    finally:
        conn.close()

    r = review(client, "trn-test-ovr", reviewer_name="ana", reviewer_role="analyst",
               decision="overridden", override_field="amount_at_risk_rupees",
               override_old_value="900.0", override_new_value="0.0", notes="nice try")
    check("override of a non-allowlisted field is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-ovr", reviewer_name="ana", reviewer_role="analyst",
               decision="overridden", override_field="agent_policy_id",
               override_old_value="POLICY-999", override_new_value="POLICY-007",
               notes="stale read")
    check("override with a stale old_value is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-ovr", reviewer_name="ana", reviewer_role="analyst",
               decision="overridden", override_field="agent_policy_id",
               override_old_value="POLICY-004", override_new_value="POLICY-007",
               notes="")
    check("override without notes is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-ovr", reviewer_name="ana", reviewer_role="analyst",
               decision="overridden", override_field="agent_policy_id",
               override_old_value="POLICY-004", override_new_value="POLICY-007",
               notes="matcher's type maps to POLICY-007, correcting the citation")
    check("a well-formed override succeeds and is terminal",
          r.status_code == 200 and r.json()["new_status"] == "overridden", r.text[:200])

    r = review(client, "trn-test-esc", reviewer_name="ana", reviewer_role="analyst",
               decision="escalated", notes="")
    check("escalation without notes is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-esc", reviewer_name="ana", reviewer_role="analyst",
               decision="escalated", notes="needs treasury input")
    check("escalation with notes succeeds",
          r.status_code == 200 and r.json()["new_status"] == "escalated", r.text[:200])

    r = review(client, "trn-test-esc", reviewer_name="ana2", reviewer_role="manager",
               decision="escalated", notes="agreed, still stuck")
    check("an escalated case can receive further escalation notes",
          r.status_code == 200 and r.json()["new_status"] == "escalated", r.text[:200])
    print()

    # ---------------------------------------------- AI auto-resolve + revert
    print("AI auto-resolve visibility and revert")
    conn = _db.get_connection()
    try:
        # Original proposal escalated (frozen, as always) -- the richer
        # investigation later found it safe to auto-resolve. This is the
        # real trn-000237 scenario, not a hypothetical.
        insert_case(conn, "trn-test-autores", 900.0, 1,
                    gate_final_decision="escalate", investigation_gate_decision="auto_resolve")
        # A case that was ALWAYS going to be auto-resolved (no investigation
        # involved, single-shot agent alone).
        insert_case(conn, "trn-test-autores2", 900.0, 1,
                    gate_final_decision="auto_resolve")
        conn.commit()
    finally:
        conn.close()

    r = client.get("/api/cases/trn-test-autores")
    body = r.json()
    check("investigation-driven auto-resolve derives status 'auto_resolved'",
          body["review_state"]["status"] == "auto_resolved", str(body["review_state"]))
    check("frozen original proposal still shows 'escalate' in gate.final_decision",
          body["gate"]["final_decision"] == "escalate", str(body["gate"]))

    r = client.get("/api/cases/trn-test-autores2")
    check("agent-level auto-resolve (no investigation) also derives 'auto_resolved'",
          r.json()["review_state"]["status"] == "auto_resolved", r.text[:200])

    r = review(client, "trn-test-autores", reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="looks fine")
    check("approving (skipping revert) an auto-resolved case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-autores", reviewer_name="ana", reviewer_role="analyst",
               decision="reverted", notes="")
    check("reverting without notes is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-autores", reviewer_name="ana", reviewer_role="analyst",
               decision="reverted", notes="disagree with the AI's auto-resolve, needs a human look")
    check("a well-formed revert succeeds and returns to 'pending'",
          r.status_code == 200 and r.json()["new_status"] == "pending", r.text[:200])

    r = review(client, "trn-test-autores", reviewer_name="ana", reviewer_role="analyst",
               decision="reverted", notes="reverting again")
    check("reverting an already-reverted (now pending) case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-autores", reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="reviewed properly this time")
    check("after revert, the normal approve flow works exactly as for any other pending case",
          r.status_code == 200 and r.json()["new_status"] == "approved", r.text[:200])

    r = client.get("/api/cases/trn-test-autores")
    activity = r.json()["activity"]
    check("activity feed has one entry per real event (proposed, investigated, reverted, approved)",
          len(activity) == 4, f"got {len(activity)}: {[a['action'] for a in activity]}")
    check("activity feed's first entry is the AI's original proposal",
          activity[0]["actor_type"] == "ai" and activity[0]["action"] == "proposed", str(activity[0]))
    check("activity feed correctly attributes the revert to the human reviewer",
          activity[2]["actor_type"] == "human" and activity[2]["action"] == "reverted", str(activity[2]))
    print()

    # ------------------------------------------------------- concurrency
    print("Optimistic concurrency")
    conn = _db.get_connection()
    try:
        insert_case(conn, "trn-test-conc", 900.0, 1)
        conn.commit()
    finally:
        conn.close()

    r = review(client, "trn-test-conc", reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="ok", expected_review_count=5)
    check("a stale expected_review_count is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-conc", reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="ok", expected_review_count=0)
    check("a correct expected_review_count is accepted",
          r.status_code == 200, r.text[:200])
    print()

    # --------------------------------------------- auto_closed transitions
    print("Auto-closed (closed-loop re-verification) transitions")
    conn = _db.get_connection()
    try:
        insert_case(conn, "trn-test-ac1", 900.0, 1)
        insert_case(conn, "trn-test-ac2", 900.0, 1)
        conn.commit()
    finally:
        conn.close()

    r = review(client, "trn-test-ac1", reviewer_name="system:closed-loop-reverification",
               reviewer_role="analyst", decision="auto_closed",
               notes="matcher no longer detects this as an exception")
    check("auto_closed is a legal transition from 'pending'",
          r.status_code == 200 and r.json()["new_status"] == "auto_closed", r.text[:200])

    r = review(client, "trn-test-ac2", reviewer_name="system:closed-loop-reverification",
               reviewer_role="analyst", decision="auto_closed", notes="")
    check("auto_closed without notes is rejected",
          r.status_code == 422, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-ac1", reviewer_name="ana", reviewer_role="analyst",
               decision="approved", notes="too late")
    check("approving an auto_closed case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-ac1", reviewer_name="ana", reviewer_role="analyst",
               decision="escalated", notes="disagree with the automated closure, reopening")
    check("escalate CAN reopen an auto_closed case",
          r.status_code == 200 and r.json()["new_status"] == "escalated", r.text[:200])

    r = review(client, TIER1_TXN, reviewer_name="system:closed-loop-reverification",
               reviewer_role="analyst", decision="auto_closed", notes="stale")
    check("auto_closed on an already-terminal (approved) case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")

    r = review(client, "trn-test-autores2", reviewer_name="system:closed-loop-reverification",
               reviewer_role="analyst", decision="auto_closed", notes="stale")
    check("auto_closed on an auto_resolved (AI fast-track) case is rejected with 409",
          r.status_code == 409, f"got {r.status_code}: {r.text[:160]}")
    print()

    # -------------------------------------------------- POST /api/reverify
    print("POST /api/reverify -- closed-loop re-verification")
    conn = _db.get_connection()
    try:
        insert_case(conn, "trn-test-rv-resolved", 900.0, 1)
        insert_case(conn, "trn-test-rv-stillopen", 900.0, 1)
        insert_case(conn, "trn-test-rv-dryrun", 900.0, 1)
        insert_case(conn, "trn-test-rv-reclassified", 900.0, 1)
        conn.commit()
        all_ids = [row["transaction_id"] for row in conn.execute("SELECT transaction_id FROM cases")]
    finally:
        conn.close()

    # The fake matcher marks EVERY currently-known transaction_id "still
    # escalated" except the specific ones this test wants resolved -- keeps
    # this section robust to whatever state earlier sections left other
    # cases in, without needing real gateway/bank/ledger data on disk.
    # RESOLVED_IDS are included as genuinely CLEAN rows (final_exception_type
    # =None), not omitted from the report entirely -- the real matcher always
    # produces a row for every ledger transaction it can see (clean or not),
    # it never just "drops" one; the reverify endpoint deliberately treats an
    # id that's MISSING from the report as unsafe to act on (see main.py's
    # own guard, added following an external review of re-verification
    # semantics), so omitting them here would test something that can't
    # actually happen instead of the real "now fully clean" case.
    RESOLVED_IDS = {"trn-test-rv-resolved", "trn-test-rv-dryrun"}
    # Reclassified to a DIFFERENT exception type that the matcher itself
    # trusts enough to auto-resolve (fee_variance) -- this is the exact
    # scenario an external review flagged: the OLD reverify logic would have
    # treated "no longer the SAME kind of trouble" as "resolved," closing a
    # case nobody ever actually reviewed for its new (if lower-risk) issue.
    RECLASSIFIED_ID = "trn-test-rv-reclassified"
    still_escalated_ids = [tid for tid in all_ids if tid not in RESOLVED_IDS and tid != RECLASSIFIED_ID]

    def fake_run_matcher(data_dir):
        report = pd.DataFrame({
            "transaction_id": still_escalated_ids + list(RESOLVED_IDS) + [RECLASSIFIED_ID],
            "final_exception_type": (["missing_bank_reference"] * len(still_escalated_ids)
                                      + [None] * len(RESOLVED_IDS) + ["fee_variance"]),
            "auto_resolve_eligible": [False] * len(still_escalated_ids) + [True] * len(RESOLVED_IDS) + [True],
        })
        return report, None, None

    _real_run_matcher = _main.run_matcher
    _main.run_matcher = fake_run_matcher
    try:
        r = client.post("/api/reverify", json={"dry_run": True})
        body = r.json()
        check("dry_run reports the resolvable cases without writing",
              r.status_code == 200 and set(body["closed"]) == RESOLVED_IDS, str(body))

        r2 = client.get("/api/cases/trn-test-rv-resolved")
        check("dry_run does not actually change status",
              r2.json()["review_state"]["status"] == "pending", str(r2.json()["review_state"]))

        r = client.post("/api/reverify", json={"dry_run": False})
        body = r.json()
        check("real run closes exactly the resolved cases, not the still-open one",
              set(body["closed"]) == RESOLVED_IDS, str(body))

        r2 = client.get("/api/cases/trn-test-rv-resolved")
        check("resolved case now derives status 'auto_closed'",
              r2.json()["review_state"]["status"] == "auto_closed", str(r2.json()["review_state"]))

        r2 = client.get("/api/cases/trn-test-rv-stillopen")
        check("case the matcher still flags stays 'pending'",
              r2.json()["review_state"]["status"] == "pending", str(r2.json()["review_state"]))

        r2 = client.get(f"/api/cases/{RECLASSIFIED_ID}")
        check("case reclassified to a DIFFERENT auto-resolvable exception type stays 'pending', "
              "not silently auto_closed",
              r2.json()["review_state"]["status"] == "pending", str(r2.json()["review_state"]))
        check("the reclassified case was never in reverify's own closed list",
              RECLASSIFIED_ID not in body["closed"], str(body))
        changed_ids = {c["transaction_id"] for c in body["changed_exception"]}
        check("reverify's response surfaces the reclassified case in changed_exception, "
              "with its original and current exception types recorded",
              changed_ids == {RECLASSIFIED_ID}
              and body["changed_exception"][0]["original_exception_type"] == "missing_bank_reference"
              and body["changed_exception"][0]["current_exception_type"] == "fee_variance",
              str(body["changed_exception"]))
        check("trn-test-rv-stillopen (unchanged exception) is in still_open, not changed_exception",
              "trn-test-rv-stillopen" in body["still_open"], str(body["still_open"]))

        r = client.post("/api/reverify", json={"dry_run": False})
        body2 = r.json()
        check("re-running reverify is idempotent -- already-closed cases aren't touched again",
              "trn-test-rv-resolved" not in body2["closed"], str(body2))

        r = client.get("/api/stats")
        check("/api/stats counts the new auto_closed cases without KeyError-ing",
              r.status_code == 200 and r.json()["counts_by_status"]["auto_closed"] >= 2, r.text[:300])
    finally:
        _main.run_matcher = _real_run_matcher
    print()

    # ------------------------------------------------------------- history
    print("Audit trail")
    r = client.get(f"/api/cases/{TIER2_TXN}")
    hist = r.json()["review_history"]
    check("every attempt that CHANGED state is recorded (2 for tier-2)",
          len(hist) == 2, f"got {len(hist)}")
    check("rejected attempts are NOT written to history",
          all(h["resulting_status"] in ("pending_manager_approval", "approved") for h in hist),
          str([h["resulting_status"] for h in hist]))
    check("reviewer identity is preserved in order",
          [h["reviewer_name"] for h in hist] == ["ana", "mgr"],
          str([h["reviewer_name"] for h in hist]))

    r = client.get("/api/stats")
    check("GET /api/stats returns 200", r.status_code == 200, r.text[:200])
    stats = r.json()
    check("stats count every seeded case",
          stats["total_cases"] == 13, str(stats["total_cases"]))
    print()

    print("=" * 70)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    finally:
        # WITH (FORCE) as a defensive fallback in case any connection to
        # the test database lingers -- Postgres otherwise refuses to drop
        # a database with active connections.
        try:
            _maint_conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(_TEST_DB_NAME))
            )
        finally:
            _maint_conn.close()
    raise SystemExit(1 if _failed else 0)
