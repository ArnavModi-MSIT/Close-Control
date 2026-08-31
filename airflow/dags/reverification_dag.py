"""Closed-loop re-verification, scheduled.

Airflow owns orchestration only -- it never imports this project's code or
touches its dependencies. This task makes one plain HTTP call to a new
endpoint on the existing review-queue FastAPI server (see
review_backend/main.py's POST /api/reverify), which re-runs the
deterministic matcher and auto-closes any case still awaiting human review
whose underlying exception has since resolved.

Target defaults to the STREAM simulator's server (port 8001,
run_stream_simulator.py) -- that's the only place in this project where
data genuinely changes over time, so it's the only place this produces
real, visible closures. Pointing this at the main static demo server
(port 8000) is legitimate too, but will honestly always report zero
closures, since data/ never changes.

host.docker.internal is Docker Desktop's DNS name for reaching a service
bound to the Windows host's loopback interface from inside a container.

Manual "Trigger DAG" from the Airflow UI works regardless of schedule --
use that for demo timing control instead of waiting on the cron tick.
"""

import datetime as dt

import requests
from airflow.sdk import DAG
from airflow.providers.standard.operators.python import PythonOperator

REVERIFY_URL = "http://host.docker.internal:8001/api/reverify"


def call_reverify(**context):
    resp = requests.post(REVERIFY_URL, json={"dry_run": False}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    # changed_exception/still_open surfaced here too (not just closed/skipped)
    # -- following an external review's observation that "still pending"
    # alone can't distinguish "the matcher re-confirmed the exact same
    # original problem" from "a different, never-reviewed exception quietly
    # replaced it" (see review_backend/main.py's reverify() docstring for the
    # underlying fix). Making that visible in the task log is what turns this
    # from a bare pass/fail check into an actual demonstration of the closed
    # loop noticing and reasoning about a real data change.
    print(f"[reverify] checked={result['checked']} closed={len(result['closed'])} "
          f"changed_exception={len(result['changed_exception'])} "
          f"still_open={len(result['still_open'])} skipped={len(result['skipped'])}")
    if result["closed"]:
        print(f"[reverify] closed transaction_ids: {result['closed']}")
    if result["changed_exception"]:
        for c in result["changed_exception"]:
            print(f"[reverify] {c['transaction_id']}: reclassified "
                  f"{c['original_exception_type']} -> {c['current_exception_type']} -- "
                  f"remains open, never silently treated as resolved")
    return result


with DAG(
    dag_id="closed_loop_reverification",
    description="Re-runs the matcher and auto-closes review-queue cases whose exception resolved",
    # tz-aware -- Airflow assumes UTC for a naive datetime anyway but warns
    # about it in the scheduler logs; found by an external review pass,
    # one-line fix, no behavior change either way.
    start_date=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    schedule="*/1 * * * *",
    catchup=False,
    max_active_runs=1,
    tags=["reconciliation"],
) as dag:
    # Bounded retries for a transient backend restart/network blip -- safe to
    # retry here specifically because the endpoint is naturally idempotent
    # per-call: a retry just re-reads whichever cases are still pending
    # (already-closed ones are excluded from candidates on the next run), so
    # a retry after a lost response finds nothing new to do rather than
    # double-processing anything.
    PythonOperator(
        task_id="call_reverify_endpoint",
        python_callable=call_reverify,
        retries=2,
        retry_delay=dt.timedelta(seconds=30),
    )
