"""
Simulated real-time transaction stream -- demo purposes only.

**This is NOT a live Razorpay integration.** There are no API credentials,
no webhooks, no network calls to Razorpay anywhere in this script or this
project. What it actually does: replays the existing seeded synthetic
dataset in `captured_at` order, releasing progressively more of it into a
"live" snapshot directory on a timer, and re-runs the real, UNMODIFIED
deterministic matcher (matching/, unchanged) plus the $0 mock agent and
gate against whatever has "arrived" so far. The point is to demonstrate
what continuous reconciliation would look like -- transactions captured,
ledger entries appearing immediately, bank postings catching up later,
exceptions surfacing and sometimes resolving as more data arrives -- using
data and code that's already fully verified, not new/untested logic.

Runs against a COMPLETELY SEPARATE data path from the main demo
(data/stream/, data/stream_audit_log.jsonl, the review_queue_stream Postgres
database -- a different database on the same local server as the main
demo's review_queue, never the same one) so it can never touch the curated
main review queue (603 real seeded cases, real LLM investigation results,
the one genuine auto-resolve). Safe to run, reset, and re-run as many
times as you like while rehearsing.

Starts its own review-queue server (same React app, same backend code,
just pointed at the stream database via REVIEW_QUEUE_DATABASE_URL) so
there's one command and one URL to watch.

    python run_stream_simulator.py
    python run_stream_simulator.py --duration-minutes 5 --tick-seconds 3
    python run_stream_simulator.py --reset
    python run_stream_simulator.py --port 8001

Ctrl+C stops both the stream and the server.
"""

import os
import sys
import json
import shutil
import pandas as pd
import argparse
import datetime as dt
import subprocess
import time

if hasattr(sys.stdout, "reconfigure"):
    # line_buffering=True matters here specifically: Python fully buffers
    # stdout (not line-buffers) whenever it's not attached to a real
    # terminal -- which includes piping to a log file, exactly how this
    # script's tick-by-tick progress gets watched during a demo/rehearsal.
    # Verified directly: without this, the tick lines were invisible in a
    # captured output file for the whole run even though the actual work
    # (confirmed via the API and the live dashboard) was happening
    # correctly the entire time -- the process just hadn't flushed yet.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
STREAM_DATA_DIR = os.path.join(DATA_DIR, "stream")
STREAM_AUDIT_LOG = os.path.join(DATA_DIR, "stream_audit_log.jsonl")
STREAM_INVESTIGATION_LOG = os.path.join(DATA_DIR, "stream_investigation_log.jsonl")

# Same local Postgres server as the main demo (postgres/docker-compose.yaml),
# a genuinely separate DATABASE on it -- review_queue_stream, never
# review_queue. Created once by postgres/init/01-create-databases.sh.
STREAM_DATABASE_URL = os.environ.get(
    "REVIEW_QUEUE_STREAM_DATABASE_URL",
    "postgresql://review_app:review_app_local_dev@localhost:5433/review_queue_stream",
)

# Set BEFORE importing anything from review_backend/agent, so both pick up
# the stream-specific database instead of the main demo's. This is the
# exact same isolation mechanism test_review_api.py uses for its own
# ephemeral database.
os.environ["REVIEW_QUEUE_DATABASE_URL"] = STREAM_DATABASE_URL
os.environ["REVIEW_QUEUE_MODE"] = "stream"

from matching.loaders import load_sources  # noqa: E402
from run_matcher import run as run_matcher  # noqa: E402
from agent import config as agent_config  # noqa: E402
from agent.client import resolve_exception, get_active_provider  # noqa: E402
from agent.gate import apply_gate  # noqa: E402
from agent.audit import write_entry  # noqa: E402
from seed_review_queue import seed as seed_review_queue  # noqa: E402
from review_backend import cache as review_cache  # noqa: E402
from cash_position.config import DEFAULT_AS_OF  # noqa: E402

agent_config.LLM_PROVIDER = "mock"
agent_config.OFFLINE_MODE = True
agent_config.AUDIT_LOG_PATH = STREAM_AUDIT_LOG


def reset_stream_state() -> None:
    if os.path.exists(STREAM_DATA_DIR):
        shutil.rmtree(STREAM_DATA_DIR)
    for f in (STREAM_AUDIT_LOG, STREAM_INVESTIGATION_LOG):
        if os.path.exists(f):
            os.remove(f)
    # No file to os.remove() for the database anymore -- it's a Postgres
    # database, not a SQLite file. TRUNCATE both tables directly (simpler
    # than a full DROP DATABASE/recreate dance for a routine --reset);
    # RESTART IDENTITY resets the id sequences too, CASCADE covers any
    # future table that ends up FK-referencing these.
    import review_backend.db as _stream_db
    _stream_db.init_db()  # idempotent -- ensures the tables exist before TRUNCATE on a fresh setup
    conn = _stream_db.get_connection()
    try:
        conn.execute("TRUNCATE reviews, cases RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()
    print("Stream state reset (data/stream/, stream_audit_log.jsonl removed; "
          "review_queue_stream database truncated).")


def already_processed_transaction_ids() -> set:
    if not os.path.exists(STREAM_AUDIT_LOG):
        return set()
    seen = set()
    with open(STREAM_AUDIT_LOG, encoding="utf-8") as f:
        for line in f:
            seen.add(json.loads(line)["transaction_id"])
    return seen


def write_filtered_snapshot(gateway, bank, ledger, simulated_now: dt.datetime) -> None:
    """Filters all three sources to what would realistically be visible by
    `simulated_now`, and writes them to STREAM_DATA_DIR in the exact
    format matching/loaders.py expects -- so run_matcher.run() works on
    this subset completely unmodified, same as it does on the real data.

    Realism, using fields that already exist (nothing invented): a
    payment is visible once captured (gateway.captured_at); its internal
    settlement ledger entry appears at the same moment (Razorpay's own
    system records an expected payment immediately, not on a delay); its
    bank posting appears later, on the bank's own credit_date -- which is
    why early in the stream you'll see plausible-looking
    "missing_bank_reference" exceptions that are really just settlement
    lag, resolving themselves as more of the stream arrives. That's
    realistic behavior, not a bug.
    """
    os.makedirs(STREAM_DATA_DIR, exist_ok=True)

    def _atomic_write(write_fn, final_path: str) -> None:
        """write_fn(tmp_path) writes the file, then this swaps it into place
        with os.replace() -- atomic on both POSIX and Windows, so a reader
        (matching/loaders.py, called from this same process each tick, but
        also now from review_backend/main.py's POST /api/reverify running
        in a completely separate process/request) always sees either the
        complete old file or the complete new one, never a half-written
        one. Without this, a read landing mid-write of gateway.json could
        observe a torn/inconsistent file -- confirmed for real: an external
        /api/reverify call hit exactly this and crashed matching/report.py
        with a bizarre "duplicate transaction_id" symptom, the actual root
        cause being a torn read, not anything wrong with that transaction's
        real data."""
        tmp_path = final_path + ".tmp"
        write_fn(tmp_path)
        os.replace(tmp_path, final_path)

    g_sub = gateway[gateway["captured_at"] <= simulated_now].copy()
    g_sub = g_sub.drop(columns=[c for c in g_sub.columns if c.endswith("_rupees")])
    _atomic_write(lambda p: g_sub.to_json(p, orient="records", indent=2),
                  os.path.join(STREAM_DATA_DIR, "gateway.json"))

    ledger_sub = ledger[ledger["transaction_id"].isin(g_sub["transaction_id_ref"])].copy()
    ledger_sub["expected_settlement_date"] = ledger_sub["expected_settlement_date"].astype(str)
    _atomic_write(lambda p: ledger_sub.to_csv(p, index=False),
                  os.path.join(STREAM_DATA_DIR, "internal_settlement_ledger.csv"))

    simulated_date = simulated_now.date()
    bank_sub = bank[bank["credit_date"] <= simulated_date].copy()
    bank_sub["credit_date"] = bank_sub["credit_date"].astype(str)
    bank_sub["value_date"] = bank_sub["value_date"].astype(str)
    _atomic_write(lambda p: bank_sub.to_csv(p, index=False),
                  os.path.join(STREAM_DATA_DIR, "bank_statement.csv"))

    return len(g_sub)


def _invalidate_stream_cache() -> None:
    """Right after each atomic snapshot write, proactively evict this run's
    two review_backend cache entries (see review_backend/cache.py) so the
    very next /api/stats or /api/reconciliation-statement poll recomputes
    from the data just written, instead of serving up-to-TTL-stale numbers.
    Uses the exact same key-builder functions main.py's endpoints use --
    never hand-formats its own copy of the key string -- so this can never
    silently drift out of sync with what the server is actually caching
    under. Degrades silently if Redis is down; review_cache.invalidate()
    itself never raises."""
    as_of_iso = DEFAULT_AS_OF.isoformat()
    review_cache.invalidate(review_cache.cash_position_stats_key(STREAM_DATA_DIR, as_of_iso))
    review_cache.invalidate(review_cache.reconciliation_statement_key(STREAM_DATA_DIR, as_of_iso))


def start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["REVIEW_QUEUE_DATABASE_URL"] = STREAM_DATABASE_URL
    env["REVIEW_QUEUE_MODE"] = "stream"
    env["CASH_POSITION_DATA_DIR"] = STREAM_DATA_DIR
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "review_backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env,
    )
    return proc


def run_tick(processed_ids: set, tick_num: int, total_ticks: int) -> dict:
    report, _, _ = run_matcher(STREAM_DATA_DIR)
    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]
    new_cases = escalated[~escalated["transaction_id"].isin(processed_ids)]

    provider = get_active_provider()
    for _, row in new_cases.iterrows():
        row_dict = row.to_dict()
        resolution = resolve_exception(row_dict)
        gate_result = apply_gate(resolution, row_dict)
        write_entry(row_dict, resolution, gate_result, provider)
        processed_ids.add(row_dict["transaction_id"])

    seed_result = seed_review_queue(
        audit_log_path=STREAM_AUDIT_LOG, data_dir=STREAM_DATA_DIR,
        investigation_log_path=STREAM_INVESTIGATION_LOG,
    )

    return {
        "arrived_total": len(report),
        "escalated_total": len(escalated),
        "new_this_tick": len(new_cases),
        "seeded_inserted": seed_result["inserted"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration-minutes", type=float, default=5.0,
                         help="Wall-clock minutes to compress the whole dataset's time range into (default: 5)")
    parser.add_argument("--tick-seconds", type=float, default=3.0,
                         help="Real seconds between each release (default: 3)")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reset", action="store_true", help="Wipe stream state before starting")
    parser.add_argument("--skip-server", action="store_true", help="Run the stream only, don't start a server")
    args = parser.parse_args()

    # Cheap sanity check on the tick math below -- a tiny --tick-seconds
    # (or a zero/negative one) would otherwise silently generate thousands
    # of full matcher runs, or divide by zero. MAX_TICKS is generous for
    # any legitimate local-demo duration/granularity combination, not a
    # tight production limit (flagged by an external review).
    MAX_TICKS = 5000
    if args.tick_seconds <= 0:
        print(f"ERROR: --tick-seconds must be > 0, got {args.tick_seconds}")
        raise SystemExit(1)
    if args.duration_minutes <= 0:
        print(f"ERROR: --duration-minutes must be > 0, got {args.duration_minutes}")
        raise SystemExit(1)
    projected_ticks = int((args.duration_minutes * 60) / args.tick_seconds)
    if projected_ticks > MAX_TICKS:
        print(f"ERROR: --duration-minutes {args.duration_minutes} / --tick-seconds "
              f"{args.tick_seconds} would produce {projected_ticks} ticks (each a full matcher "
              f"run) -- over the {MAX_TICKS} sanity cap. Use a larger --tick-seconds or a "
              f"shorter --duration-minutes.")
        raise SystemExit(1)

    print()
    print("  SIMULATED REAL-TIME RECONCILIATION STREAM")
    print("  (existing synthetic dataset, replayed in time order -- not a live Razorpay feed)")
    print()

    if args.reset:
        reset_stream_state()

    print("Loading full dataset (read-only -- the real data/ files are never modified)...")
    gateway, bank, ledger = load_sources(DATA_DIR)

    # end_time must cover the LATEST relevant date across all three
    # sources, not just gateway.captured_at -- bank postings settle days
    # after capture (T+2 typically, sometimes later), so bank.credit_date
    # extends well past gateway.captured_at.max(). Capping the range at
    # captured_at alone (an earlier version of this script did exactly
    # that) meant the simulation never reached a state equivalent to the
    # full static dataset even at its final tick -- verified: it produced
    # 731 "escalated" cases at the end instead of the known-correct 603,
    # because the last ~5 days of real bank settlements were permanently
    # excluded. Adding one full day of buffer past bank's date-only max
    # (it has no time-of-day component) so that entire final day is
    # genuinely included, not excluded by an off-by-a-few-hours boundary.
    start_time = gateway["captured_at"].min()
    end_time = max(
        gateway["captured_at"].max(),
        gateway["settled_at"].max(),
        pd.Timestamp(bank["credit_date"].max()) + pd.Timedelta(days=1),
    )
    total_range = end_time - start_time
    num_ticks = max(1, int((args.duration_minutes * 60) / args.tick_seconds))
    step = total_range / num_ticks

    print(f"Dataset time range: {start_time} .. {end_time} ({total_range})")
    print(f"Compressed into {args.duration_minutes} min, released over {num_ticks} ticks "
          f"(~{step} of simulated time per tick, every {args.tick_seconds}s of real time)")
    print()

    server_proc = None
    if not args.skip_server:
        print(f"Starting review-queue server on port {args.port} (stream database, isolated from the main demo)...")
        server_proc = start_server(args.port)
        time.sleep(1.5)
        print(f"  Open: http://127.0.0.1:{args.port}/review-queue/")
    print()

    processed_ids = already_processed_transaction_ids()
    if processed_ids:
        print(f"Resuming prior stream state: {len(processed_ids)} transaction(s) already "
              f"processed in a prior run (use --reset to start clean).")
    else:
        print("Fresh demo run: no prior processed transactions.")
    print()

    try:
        simulated_now = start_time
        for tick in range(1, num_ticks + 1):
            simulated_now = min(simulated_now + step, end_time)
            write_filtered_snapshot(gateway, bank, ledger, simulated_now)
            _invalidate_stream_cache()
            result = run_tick(processed_ids, tick, num_ticks)
            print(f"[tick {tick:>3}/{num_ticks}] simulated time: {simulated_now}  |  "
                  f"{result['arrived_total']} arrived, {result['escalated_total']} escalated total, "
                  f"{result['new_this_tick']} new case(s) this tick")
            if tick < num_ticks:
                time.sleep(args.tick_seconds)
    except KeyboardInterrupt:
        print("\nStream stopped.")
    finally:
        print()
        print("Stream finished. Review queue server " +
              ("still running -- Ctrl+C to stop it." if server_proc else "was not started (--skip-server)."))
        if server_proc:
            try:
                server_proc.wait()
            except KeyboardInterrupt:
                server_proc.terminate()
                server_proc.wait()


if __name__ == "__main__":
    main()
