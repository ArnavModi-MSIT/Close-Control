"""Human review queue -- FastAPI backend.

Serves the API (/api/*), the built React review-queue app (/review-queue/,
source in ui/review-queue-app/), and the still-static ui/ files (showcase.html
etc -- deliberately kept vanilla HTML/CSS/JS since that page is meant to be
hosted separately on GitHub Pages with no backend) all from one origin/port,
so there's no CORS to configure for a local demo.

    uvicorn review_backend.main:app --reload

Then open http://127.0.0.1:8000/review-queue/ (or just http://127.0.0.1:8000/,
which redirects there). The React app must be built first --
cd ui/review-queue-app && npm install && npm run build.

This process only ever INSERTs into `reviews`; it never UPDATEs or DELETEs
a `cases` row (that table is owned by seed_review_queue.py). See CLAUDE.md
for the full design rationale.
"""

import json
import os
import uuid
import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from . import db
from . import cache
from . import sla
from . import chain
from . import cycle_time
from .config import MANAGER_APPROVAL_THRESHOLD_RUPEES
from .models import ReviewSubmission, ReverificationRequest, BulkReviewRequest, QARequest
from .state_machine import next_status, InvalidTransition, APPLICATION_VERSION, OPEN_STATUSES

from run_matcher import run as run_matcher
from matching.loaders import load_sources
from matching.root_cause import cluster_escalated_cases, summarize
from matching.loaders import load_loan_book
from qa_agent.tools import ToolContext as QAToolContext
from qa_agent.loop import ask as qa_ask
from qa_agent import config as qa_config
from investigator.ollama_client import OllamaToolClient
from journal_entries import build_journal_entry
from cash_position.engine import build_cash_position
from cash_position.config import DEFAULT_AS_OF
from cash_position.reconciliation_statement import build_reconciliation_statement
from corrections import append_correction

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")

# Same isolation pattern as REVIEW_QUEUE_DATABASE_URL -- run_stream_simulator.py
# points this at data/stream/ so /api/stats' money figures stay consistent
# with whichever dataset the database is actually reflecting, instead of
# always reading the static main demo dataset regardless of mode.
CASH_POSITION_DATA_DIR = os.environ.get(
    "CASH_POSITION_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)

# Safety-net TTL only -- run_stream_simulator.py actively invalidates its
# own keys right after every tick (see there), so this mostly matters for
# the static main demo (which has no tick to hook into at all) and for the
# rare case an invalidation call itself gets missed (Redis down at that
# exact moment). ~2-3x STATS_POLL_MS's 3-second poll interval
# (ui/review-queue-app/src/hooks/useQueries.ts) and >= the stream
# simulator's own default --tick-seconds=3, so it should essentially never
# be the thing serving stale data under normal operation.
CASH_POSITION_CACHE_TTL_SECONDS = 8
RECONCILIATION_STATEMENT_CACHE_TTL_SECONDS = 8
ROOT_CAUSE_CLUSTERS_CACHE_TTL_SECONDS = 8


def _cash_position_stats() -> dict | None:
    """Served through a short-TTL, best-effort Redis cache (see
    review_backend/cache.py) rather than computed on every call --
    STATS_POLL_MS=3000's unconditional 3-second poll
    (ui/review-queue-app/src/hooks/useQueries.ts) was re-running the full
    matcher forever, even against the static main demo dataset, which
    never changes at all once generated. Redis is a pure performance
    layer, never a hard dependency the way Postgres is: any Redis error
    (down, timeout, never started) falls straight through to computing
    this directly, just slower -- identical to how this function always
    behaved before caching existed. The cache key includes
    CASH_POSITION_DATA_DIR (the existing per-instance differentiator, see
    its own comment above) and DEFAULT_AS_OF, so the main demo and a
    concurrently-running stream demo sharing one Redis server can never
    read each other's numbers, and a code change to DEFAULT_AS_OF can
    never serve a stale as-of date's cached figures under new code.
    run_stream_simulator.py's tick loop also actively invalidates its own
    key right after every atomic snapshot write, so a poll shortly after a
    tick still sees fresh data rather than a stale cached value.
    Returns None (not an error) if the data isn't in a shape this can
    score yet -- a money widget failing to render is not worth taking the
    whole stats endpoint down for."""
    def _compute():
        try:
            report, _, _ = run_matcher(CASH_POSITION_DATA_DIR)
            gateway, _, _ = load_sources(CASH_POSITION_DATA_DIR)
            result = build_cash_position(report, gateway, DEFAULT_AS_OF)
            snap = result["snapshot"]
            automated_mask = report["final_exception_type"].isna() | report["auto_resolve_eligible"]
            automation_rate = float(automated_mask.mean())
            return {
                "as_of": DEFAULT_AS_OF.isoformat(),
                "confirmed_rupees": round(snap["confirmed_rupees"], 2),
                "in_transit_rupees": round(snap["in_transit_rupees"], 2),
                "at_risk_rupees": round(snap["held_rupees"] + snap["at_risk_due_nominal_rupees"], 2),
                "projected_cash_position_rupees": round(snap["projected_cash_position_rupees"], 2),
                "automation_rate_pct": round(automation_rate * 100, 1),
                # The KPI card only ever showed the percentage, with no
                # visible anchor for what it's a percentage OF -- 617 (the
                # escalated-queue count shown elsewhere on the same page)
                # is a completely different, smaller number, so a reader
                # had no way to tell 70.2% was out of 2,072, not 617.
                # Exposed explicitly rather than left implicit in a tooltip.
                "automation_numerator": int(automated_mask.sum()),
                "total_ledger_transactions": int(len(report)),
            }
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            print(f"[WARN] cash position unavailable for /api/stats: {type(e).__name__}: {e}")
            return None

    key = cache.cash_position_stats_key(CASH_POSITION_DATA_DIR, DEFAULT_AS_OF.isoformat())
    return cache.cached_or_compute(key, CASH_POSITION_CACHE_TTL_SECONDS, _compute)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Reconciliation Review Queue", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    """StaticFiles serves ui/ with only ETag/Last-Modified, no Cache-Control
    -- browsers apply heuristic freshness caching on that (commonly ~10% of
    the file's age since Last-Modified) and can silently reuse a stale
    styles.css/review-queue.js across page loads, even in a brand new tab,
    without ever re-contacting the server. Verified directly: after editing
    styles.css and restarting the server, a fresh navigation still rendered
    the OLD CSS (confirmed via document.styleSheets showing the old rule
    text) until this header was added. For a local single-developer demo
    tool there's no meaningful cost to disabling caching entirely, and it's
    the only way to guarantee "the page I'm looking at matches the file on
    disk" during iterative development."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------- helpers

def _row_to_case_dict(row) -> dict:
    d = dict(row)
    d["reclassified"] = bool(d["reclassified"])
    d["policy_id_consistent"] = bool(d["policy_id_consistent"])
    d["agent_sufficient_evidence"] = bool(d["agent_sufficient_evidence"])
    d["gate_reasons"] = json.loads(d["gate_reasons"])
    d["gate_condition_checks"] = json.loads(d["gate_condition_checks"]) if d["gate_condition_checks"] else None
    d["all_signals"] = json.loads(d["all_signals"]) if d["all_signals"] else []
    d["evidence_fields_cited"] = json.loads(d["evidence_fields_cited"])
    d["investigated"] = bool(d["investigated"])
    d["investigation_log"] = json.loads(d["investigation_log"]) if d["investigation_log"] else []
    return d


def _row_to_review_dict(row) -> dict:
    return dict(row)


def _get_case_row(conn, transaction_id: str):
    row = conn.execute("SELECT * FROM cases WHERE transaction_id = %s", (transaction_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no case found for transaction_id {transaction_id!r}")
    return row


def _get_reviews(conn, transaction_id: str):
    return conn.execute(
        "SELECT * FROM reviews WHERE transaction_id = %s ORDER BY id ASC", (transaction_id,)
    ).fetchall()


def _latest_review_status_by_txn(conn) -> dict:
    """{transaction_id: latest resulting_status} for EVERY case, in one query.

    Status derivation only ever needs the most recent review's
    resulting_status (see _derive_status_from_latest below), never the full
    history -- so any endpoint that derives status for many cases at once
    (/api/stats, /api/cases) can use this instead of calling _get_reviews()
    once per case. That per-case pattern was a genuine N+1: measured at 604
    queries / 0.53s against the real 603-case database versus 1 query /
    0.012s here, a 45x difference, on an endpoint the frontend polls every
    3 seconds and which deliberately cannot be Redis-cached (it must reflect
    live review state, see cache.py).

    DISTINCT ON is Postgres-specific and intentional -- this codebase
    targets Postgres only now (see db.py's module docstring on the SQLite
    migration), and it lets the database return one row per transaction
    instead of shipping every review row to Python to be reduced.
    """
    rows = conn.execute(
        "SELECT DISTINCT ON (transaction_id) transaction_id, resulting_status "
        "FROM reviews ORDER BY transaction_id, id DESC"
    ).fetchall()
    return {r["transaction_id"]: r["resulting_status"] for r in rows}


def _review_count_by_txn(conn) -> dict:
    """{transaction_id: review count} for EVERY case with at least one
    review, in one query -- same N+1-avoidance shape as
    _latest_review_status_by_txn above, for the one other piece of
    per-case review data a bulk endpoint sometimes needs
    (expected_review_count's optimistic-concurrency guard). A transaction_id
    missing from this dict has zero reviews; callers should .get(id, 0)."""
    rows = conn.execute(
        "SELECT transaction_id, COUNT(*) AS n FROM reviews GROUP BY transaction_id"
    ).fetchall()
    return {r["transaction_id"]: r["n"] for r in rows}


def _derive_status_from_latest(case_row, latest_status: str | None) -> str:
    """The single source of truth for how a case's status is derived.
    Takes just the latest review's resulting_status (or None if the case has
    no reviews yet) rather than the whole list, so both the one-case path
    (_derive_status) and the all-cases path (_latest_review_status_by_txn)
    apply identical rules."""
    if latest_status is not None:
        return latest_status
    # No human action yet -- the case's initial status depends on what the
    # gate itself decided. Prefer the investigation's own gate outcome when
    # one exists (it's the richer, later signal -- see db.py's migration
    # comment for investigation_gate_decision); fall back to the frozen
    # original proposal's outcome otherwise.
    decision = case_row["investigation_gate_decision"] or case_row["gate_final_decision"]
    return "auto_resolved" if decision == "auto_resolve" else "pending"


def _derive_status(case_row, reviews) -> str:
    return _derive_status_from_latest(
        case_row, reviews[-1]["resulting_status"] if reviews else None)


def _build_activity(cd: dict, reviews) -> list[dict]:
    """Unified, chronological feed of everything that's happened to this
    case -- AI proposal, AI investigation (if any), every human review
    action -- so "what did the AI and the humans do" is one timeline
    instead of three separately-shaped sections a reader has to mentally
    merge themselves. Ordered by construction (proposal -> investigation
    -> reviews in their stored order), not by sorting raw timestamps --
    some older investigation entries predate timestamp tracking and are
    None, which Python can't compare against a real ISO string."""
    proposed_detail = f"Classified as {cd['agent_exception_type']}"
    if cd["reclassified"]:
        proposed_detail += " (reclassified from the matcher's own type)"
    if cd["agent_confidence"] is not None:
        proposed_detail += f", confidence {cd['agent_confidence']:.2f}"

    activity = [{
        "actor": f"AI ({cd['provider'] or 'unknown provider'})",
        "actor_type": "ai",
        "action": "proposed",
        "timestamp": cd["agent_decided_at"] or cd["seeded_at"],
        "detail": proposed_detail,
    }]

    if cd["investigated"]:
        outcome = cd["investigation_gate_decision"] or "escalate"
        inv_detail = f"Ran {cd['investigation_tool_rounds'] or 0} tool round(s); gate decision: {outcome}"
        if outcome == "auto_resolve":
            inv_detail += " -- AUTO-RESOLVED, no human input yet"
        activity.append({
            "actor": "AI (investigation agent)",
            "actor_type": "ai",
            "action": "investigated",
            "timestamp": cd["investigation_investigated_at"],
            "detail": inv_detail,
        })

    for r in reviews:
        detail = f"-> {r['resulting_status']}"
        if r["notes"]:
            detail += f" -- {r['notes']}"
        # The closed-loop re-verification job (POST /api/reverify) submits
        # reviews through this exact same table/endpoint as a real human
        # would, identified only by a "system:" reviewer_name prefix --
        # surface that distinction here rather than mislabeling it "human".
        is_system = r["reviewer_name"].startswith("system:")
        activity.append({
            "actor": r["reviewer_name"] if is_system else f"{r['reviewer_name']} ({r['reviewer_role']})",
            "actor_type": "system" if is_system else "human",
            "action": r["decision"],
            "timestamp": r["created_at"],
            "detail": detail,
        })

    return activity


def _prior_analyst_reviewer(reviews) -> str | None:
    for r in reversed(reviews):
        if r["decision"] == "approved" and r["reviewer_role"] == "analyst" \
                and r["resulting_status"] == "pending_manager_approval":
            return r["reviewer_name"]
    return None


# ------------------------------------------------------------------ API

@app.get("/api/cases")
def list_cases(exception_type: str | None = None, status: str | None = None,
                min_amount: float | None = None, max_amount: float | None = None,
                search: str | None = None,
                sort: str = "amount_at_risk_rupees", sort_direction: str = "desc",
                page: int = 1, page_size: int = 25):
    if page < 1:
        raise HTTPException(400, "page must be >= 1")
    if not (1 <= page_size <= 200):
        raise HTTPException(400, "page_size must be between 1 and 200")
    allowed_sort = {"amount_at_risk_rupees", "agent_confidence", "transaction_id", "seeded_at"}
    if sort not in allowed_sort:
        raise HTTPException(400, f"sort must be one of {sorted(allowed_sort)}")
    if sort_direction not in ("asc", "desc"):
        raise HTTPException(400, "sort_direction must be 'asc' or 'desc'")

    conn = db.get_connection()
    try:
        clauses, params = [], []
        if exception_type:
            clauses.append("matcher_exception_type = %s")
            params.append(exception_type)
        if min_amount is not None:
            clauses.append("amount_at_risk_rupees >= %s")
            params.append(min_amount)
        if max_amount is not None:
            clauses.append("amount_at_risk_rupees <= %s")
            params.append(max_amount)
        if search:
            # Escape LIKE's own wildcards so a literal "%" or "_" in a
            # search term (unlikely for a transaction ID, but not
            # impossible) matches itself rather than being interpreted as
            # a wildcard -- a search box should never behave like a
            # pattern-matching field the user didn't ask for. ILIKE (not
            # LIKE) to preserve SQLite's case-insensitive-by-default search
            # behavior -- Postgres's own LIKE is case-sensitive.
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("transaction_id ILIKE %s ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        # deterministic secondary sort key so pagination never reorders
        # ties between requests
        rows = conn.execute(
            f"SELECT * FROM cases {where} ORDER BY {sort} {sort_direction}, transaction_id ASC",
            params,
        ).fetchall()

        # One query for every case's latest review status, instead of one
        # query per case inside the loop below (the same N+1 that /api/stats
        # had -- see _latest_review_status_by_txn's docstring for the
        # measured 45x difference).
        latest_status = _latest_review_status_by_txn(conn)

        # Only a case still awaiting a human can breach its SLA -- one that's
        # already approved/overridden/escalated/auto-closed was acted on, so
        # ageing it further would be misleading.
        cases = []
        for row in rows:
            case_status = _derive_status_from_latest(row, latest_status.get(row["transaction_id"]))
            if status and case_status != status:
                continue
            cd = _row_to_case_dict(row)
            case_sla = (sla.sla_for_case(cd["transaction_id"], DEFAULT_AS_OF, CASH_POSITION_DATA_DIR)
                        if case_status in OPEN_STATUSES
                        else {"sla_deadline": None, "sla_days_overdue": 0, "sla_breached": False})
            cases.append({
                "transaction_id": cd["transaction_id"],
                "matcher_exception_type": cd["matcher_exception_type"],
                "amount_at_risk_rupees": cd["amount_at_risk_rupees"],
                "required_approval_tier": cd["required_approval_tier"],
                "agent_confidence": cd["agent_confidence"],
                "gate_final_decision": cd["gate_final_decision"],
                "status": case_status,
                "investigated": cd["investigated"],
                "resolution_source": cd["resolution_source"],
                "sla_days_overdue": case_sla["sla_days_overdue"],
                "sla_breached": case_sla["sla_breached"],
            })

        total = len(cases)
        start = (page - 1) * page_size
        page_items = cases[start:start + page_size]
        return {"items": page_items, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


@app.get("/api/cases/{transaction_id}")
def get_case(transaction_id: str):
    conn = db.get_connection()
    try:
        row = _get_case_row(conn, transaction_id)
        cd = _row_to_case_dict(row)
        reviews = _get_reviews(conn, transaction_id)
        case_status = _derive_status(row, reviews)

        return {
            "case": {
                "transaction_id": cd["transaction_id"],
                "merchant_id": cd["merchant_id"],
                "settlement_id": cd["settlement_id"],
                "matcher_exception_type": cd["matcher_exception_type"],
                "amount_at_risk_rupees": cd["amount_at_risk_rupees"],
                "required_approval_tier": cd["required_approval_tier"],
                "risk_class": cd["risk_class"],
            },
            "ai_proposal": {
                "agent_exception_type": cd["agent_exception_type"],
                "reclassified": cd["reclassified"],
                "agent_root_cause": cd["agent_root_cause"],
                "agent_recommended_action": cd["agent_recommended_action"],
                "agent_confidence": cd["agent_confidence"],
                "agent_policy_id": cd["agent_policy_id"],
                "policy_id_consistent": cd["policy_id_consistent"],
                "agent_sufficient_evidence": cd["agent_sufficient_evidence"],
                "provider": cd["provider"],
                "model": cd["model"],
                "resolution_source": cd["resolution_source"],
            },
            "gate": {
                "final_decision": cd["gate_final_decision"],
                "reasons": cd["gate_reasons"],
                # structured PASS/FAIL per condition, null for cases seeded
                # before this field existed (see agent/gate.py)
                "condition_checks": cd["gate_condition_checks"],
            },
            "evidence": {
                "match_status": cd["match_status"],
                "match_pass": cd["match_pass"],
                "ledger_expected_net_rupees": cd["ledger_expected_net_rupees"],
                "observed_net_rupees": cd["observed_net_rupees"],
                "net_delta_rupees": cd["net_delta_rupees"],
                "all_signals": cd["all_signals"],
                "fields_cited": cd["evidence_fields_cited"],
            },
            "investigation": {
                "investigated": cd["investigated"],
                "summary": cd["investigation_summary"],
                "drafted_communication": cd["investigation_drafted_communication"],
                "tool_rounds": cd["investigation_tool_rounds"],
                "log": cd["investigation_log"],
                "gate_decision": cd["investigation_gate_decision"],
                "investigated_at": cd["investigation_investigated_at"],
            } if cd["investigated"] else None,
            "review_state": {
                "status": case_status,
                "review_count": len(reviews),
                "awaiting_role": "manager" if case_status == "pending_manager_approval" else
                                 ("analyst" if case_status == "pending" else None),
            },
            # RBI TAT position -- only meaningful while a case is still
            # awaiting a human; see review_backend/sla.py.
            "sla": sla.sla_for_case(cd["transaction_id"], DEFAULT_AS_OF, CASH_POSITION_DATA_DIR)
                   if case_status in OPEN_STATUSES else None,
            "review_history": [_row_to_review_dict(r) for r in reviews],
            "activity": _build_activity(cd, reviews),
            # Deterministic double-entry journal-entry draft (journal_entries.py)
            # -- "run the books," taken literally. No LLM involved: the
            # accounting treatment per exception_type is fixed, known
            # practice, not a case-by-case judgment call, so this is built
            # entirely from fields already stored on this case row, the
            # same "AI proposes nothing here, it's 100% deterministic"
            # discipline as cash_position/ and matching/root_cause.py.
            "journal_entry": build_journal_entry({
                "transaction_id": cd["transaction_id"],
                "final_exception_type": cd["matcher_exception_type"],
                "ledger_expected_net_rupees": cd["ledger_expected_net_rupees"],
                "observed_net_rupees": cd["observed_net_rupees"],
                "net_delta_rupees": cd["net_delta_rupees"],
            }),
            "provenance": {
                "seeded_at": cd["seeded_at"],
                "audit_log_source": cd["audit_log_source"],
                "audit_record_hash": cd["audit_record_hash"],
                "schema_version": cd["schema_version"],
            },
        }
    finally:
        conn.close()


@app.post("/api/cases/{transaction_id}/review")
def submit_review(transaction_id: str, submission: ReviewSubmission):
    conn = db.get_connection()
    try:
        case_row = _get_case_row(conn, transaction_id)
        reviews = _get_reviews(conn, transaction_id)

        if submission.expected_review_count is not None \
                and submission.expected_review_count != len(reviews):
            raise HTTPException(
                409, f"case has changed since you loaded it (expected "
                     f"{submission.expected_review_count} prior reviews, found {len(reviews)}) "
                     f"-- refresh and retry")

        current_status = _derive_status(case_row, reviews)
        tier = case_row["required_approval_tier"]

        if submission.decision == "overridden":
            current_value = case_row[submission.override_field]
            if str(current_value) != str(submission.override_old_value):
                raise HTTPException(
                    422, f"stale override: {submission.override_field} is currently "
                         f"{current_value!r}, not {submission.override_old_value!r} as submitted "
                         f"-- someone else may have changed it, refresh and retry")

        try:
            new_status = next_status(
                current_status=current_status,
                tier=tier,
                decision=submission.decision,
                role=submission.reviewer_role,
                reviewer_name=submission.reviewer_name,
                prior_analyst_reviewer=_prior_analyst_reviewer(reviews),
            )
        except InvalidTransition as e:
            raise HTTPException(e.http_status, str(e))

        review_uuid = str(uuid.uuid4())
        created_at = dt.datetime.now(dt.timezone.utc).isoformat()
        review_fields = {
            "review_uuid": review_uuid, "transaction_id": transaction_id,
            "reviewer_name": submission.reviewer_name, "reviewer_role": submission.reviewer_role,
            "decision": submission.decision, "override_field": submission.override_field,
            "override_old_value": submission.override_old_value,
            "override_new_value": submission.override_new_value, "notes": submission.notes,
            "previous_status": current_status, "resulting_status": new_status,
            "created_at": created_at, "application_version": APPLICATION_VERSION,
        }
        # Computed inside the SAME transaction as the insert below, before
        # it -- see chain.next_chain_hash()'s own docstring for why the
        # advisory lock it acquires must span both steps to be race-safe
        # against a concurrent reviewer doing the same thing.
        chain_hash = chain.next_chain_hash(conn, review_fields)
        conn.execute(
            """INSERT INTO reviews
               (review_uuid, transaction_id, reviewer_name, reviewer_role, decision,
                override_field, override_old_value, override_new_value, notes,
                previous_status, resulting_status, created_at, application_version, chain_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (review_uuid, transaction_id, submission.reviewer_name, submission.reviewer_role,
             submission.decision, submission.override_field, submission.override_old_value,
             submission.override_new_value, submission.notes, current_status, new_status,
             created_at, APPLICATION_VERSION, chain_hash),
        )
        conn.commit()

        if submission.decision == "overridden":
            # Written AFTER the review itself committed successfully --
            # never record a correction for a review that didn't actually
            # persist. Best-effort: a write failure here must never turn a
            # successful review submission into a 500 (the correction
            # memory is an enhancement to future prompts, never part of
            # this request's own contract) -- see corrections.py's
            # docstring for the full design.
            try:
                append_correction(
                    CASH_POSITION_DATA_DIR, transaction_id=transaction_id,
                    matcher_exception_type=case_row["matcher_exception_type"],
                    override_field=submission.override_field,
                    override_old_value=submission.override_old_value,
                    override_new_value=submission.override_new_value,
                    reason=submission.notes, reviewer_name=submission.reviewer_name,
                    created_at=created_at,
                )
            except OSError as e:  # noqa: BLE001 -- deliberately narrow+non-fatal, see comment above
                print(f"[WARN] could not append correction for {transaction_id}: {e}")

        return {
            "review_uuid": review_uuid,
            "transaction_id": transaction_id,
            "new_status": new_status,
            "created_at": created_at,
        }
    finally:
        conn.close()


@app.post("/api/cases/bulk-review")
def bulk_review(payload: BulkReviewRequest):
    """Apply ONE review decision to a whole set of cases at once -- the
    review-side counterpart to GET /api/root-cause-clusters: a reviewer who
    trusts a cluster's diagnosis (e.g. "these 47 cases are all the same
    missing-bank-reference settlement") can act on it as one thing instead
    of clicking through every case individually.

    Deliberately reuses submit_review() UNCHANGED, once per transaction_id
    -- this is not a new state-machine path, it is the existing single-case
    path called N times. Every case still individually validates against
    state_machine.py's rules (tier, current status, terminal-state guards),
    so a case that has since moved to a state where this decision is no
    longer legal is skipped and reported, never silently forced through.

    Two-pass concurrency design, mirroring POST /api/reverify exactly: pass
    1 reads each case's CURRENT review count (a short read-only sweep);
    pass 2 submits each review with that count as expected_review_count, so
    a case a different reviewer touched in the moments between the two
    passes is safely rejected (409) rather than silently overwritten --
    same optimistic-concurrency guard every single-case review already
    gets, just captured explicitly here since there is no client-loaded
    page state to compare against for a bulk action.
    """
    conn = db.get_connection()
    try:
        review_counts: dict[str, int] = {}
        missing: list[str] = []
        for transaction_id in payload.transaction_ids:
            row = conn.execute(
                "SELECT 1 FROM cases WHERE transaction_id = %s", (transaction_id,)
            ).fetchone()
            if row is None:
                missing.append(transaction_id)
                continue
            review_counts[transaction_id] = len(_get_reviews(conn, transaction_id))
    finally:
        conn.close()

    results = []
    for transaction_id in missing:
        results.append({"transaction_id": transaction_id, "outcome": "skipped",
                         "new_status": None, "reason": "no case found"})

    for transaction_id, review_count in review_counts.items():
        submission = ReviewSubmission(
            reviewer_name=payload.reviewer_name,
            reviewer_role=payload.reviewer_role,
            decision=payload.decision,
            notes=payload.notes,
            expected_review_count=review_count,
        )
        try:
            result = submit_review(transaction_id, submission)
            results.append({"transaction_id": transaction_id, "outcome": "reviewed",
                             "new_status": result["new_status"], "reason": None})
        except HTTPException as e:
            results.append({"transaction_id": transaction_id, "outcome": "skipped",
                             "new_status": None, "reason": str(e.detail)})

    return {
        "requested": len(payload.transaction_ids),
        "reviewed_count": sum(1 for r in results if r["outcome"] == "reviewed"),
        "skipped_count": sum(1 for r in results if r["outcome"] == "skipped"),
        "results": results,
    }


@app.get("/api/root-cause-clusters")
def root_cause_clusters():
    """Collapses the escalated queue into its underlying root causes (see
    matching/root_cause.py) -- computed live against CASH_POSITION_DATA_DIR,
    same short-TTL best-effort Redis cache pattern as
    _cash_position_stats()/reconciliation_statement() above. Purely derived
    from the matcher's report; never touches Postgres or the cases table,
    so a cluster's case_count can differ momentarily from the review
    queue's own counts if a case was just approved (the review action
    doesn't change what the MATCHER sees, only what a human decided about
    it) -- same "computed fresh, not from stored state" contract as the
    reconciliation statement above.
    """
    def _compute():
        report, _, _ = run_matcher(CASH_POSITION_DATA_DIR)
        escalated_count = int(
            (report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])).sum()
        )
        clusters = cluster_escalated_cases(report)
        return {
            "summary": summarize(clusters, escalated_count),
            "clusters": clusters.to_dict(orient="records"),
        }

    key = cache.root_cause_clusters_key(CASH_POSITION_DATA_DIR)
    return cache.cached_or_compute(key, ROOT_CAUSE_CLUSTERS_CACHE_TTL_SECONDS, _compute)


@app.post("/api/reverify")
def reverify(payload: ReverificationRequest = ReverificationRequest()):
    """Closed-loop re-verification: re-runs the deterministic matcher against
    whatever CASH_POSITION_DATA_DIR currently holds and auto-closes any case
    still awaiting human review whose transaction the matcher now reports as
    FULLY CLEAN (final_exception_type is None) -- e.g. a missing_bank_reference
    case where the bank posting has since arrived. Deliberately NOT "the
    original exception type is no longer present" -- a transaction whose
    exception merely changed to a different, still-technically-matcher-
    auto-resolve-eligible type (e.g. missing_bank_reference -> fee_variance)
    stays open, since that's a real, different, never-reviewed condition,
    not evidence the original problem was actually resolved (found via
    external review). Never touches a case a human has already decided
    (approved/overridden/escalated) or one the gate itself fast-tracked
    (auto_resolved) -- see state_machine.py's auto_closed branch.

    Against the static main demo dataset this always reports zero closures
    (the data never changes), which is expected, not a bug -- it's only
    run_stream_simulator.py's progressively-revealed data where a case's
    underlying condition can genuinely resolve between two calls."""
    report, _, _ = run_matcher(CASH_POSITION_DATA_DIR)
    # Indexed by transaction_id -> the CURRENT final_exception_type (None if
    # now fully clean). Deliberately NOT "is this still in the escalated-
    # and-not-auto-resolve-eligible set" (the old check) -- that conflates
    # "the original condition is gone" with "no exception at all remains,"
    # and those are different: a transaction originally escalated as
    # missing_bank_reference could re-run as fee_variance, which IS
    # matcher-auto-resolve-eligible and so would have dropped out of that
    # set even though a real (if lower-risk) exception still exists, one
    # nobody -- human or AI -- ever actually reviewed. Found via external
    # review: only close when the transaction is genuinely clean now, never
    # merely "no longer the SAME kind of trouble."
    current_exception_by_txn = report.set_index("transaction_id")["final_exception_type"]
    is_clean_by_txn = current_exception_by_txn.isna()

    # Pass 1: short-lived read-only connection, just to categorize every
    # still-open case -- not just the closure candidates. `changed_exception`
    # and `still_open` are informational only (no state transition exists
    # for them in state_machine.py, and none is added here -- a case that
    # merely got reclassified stays exactly 'pending', simply with this run
    # now having SEEN and recorded that fact in the response), but that
    # visibility is real demo/audit value: without it, "still pending" looks
    # identical whether the matcher re-confirmed the SAME original problem
    # or quietly swapped in a different one nobody has reviewed yet (found
    # via external review).
    conn = db.get_connection()
    try:
        # Was one query for ALL cases plus 2 queries PER CASE
        # (_get_case_row/_get_reviews inside the loop below) -- the exact
        # N+1 shape this codebase already measured and fixed once for
        # /api/stats/api/cases (see _latest_review_status_by_txn's own
        # docstring, 45x difference). Reintroduced here since this endpoint
        # predates that fix; closed the same way, reusing the same two
        # bulk-query helpers instead of a third bespoke one -- found via
        # external review, worth fixing specifically here since Airflow
        # triggers this endpoint every minute (see reverification_dag.py).
        rows = conn.execute(
            "SELECT transaction_id, matcher_exception_type, investigation_gate_decision, "
            "gate_final_decision FROM cases"
        ).fetchall()
        latest_status = _latest_review_status_by_txn(conn)
        review_counts = _review_count_by_txn(conn)
        closed_candidates, changed_exception, still_open = [], [], []
        for row in rows:
            transaction_id = row["transaction_id"]
            status = _derive_status_from_latest(row, latest_status.get(transaction_id))
            if status not in OPEN_STATUSES:
                continue  # already decided (by a human or the gate) -- not eligible for auto-closure
            review_count = review_counts.get(transaction_id, 0)

            if transaction_id not in is_clean_by_txn.index:
                still_open.append(transaction_id)  # can't currently observe it -- leave alone, not "clean"
            elif is_clean_by_txn[transaction_id]:
                closed_candidates.append((transaction_id, review_count, row["matcher_exception_type"]))
            else:
                current_type = current_exception_by_txn[transaction_id]  # non-null here, is_clean_by_txn was False
                if current_type != row["matcher_exception_type"]:
                    changed_exception.append({
                        "transaction_id": transaction_id,
                        "original_exception_type": row["matcher_exception_type"],
                        "current_exception_type": current_type,
                    })
                else:
                    still_open.append(transaction_id)
    finally:
        conn.close()

    closed, skipped = [], []
    if not payload.dry_run:
        # Pass 2: no connection held open across this loop -- each
        # submit_review() call is its own self-contained unit (opens,
        # reads, writes, commits, closes), same as a real HTTP request
        # would do. expected_review_count (captured in pass 1) is the
        # existing optimistic-concurrency guard that protects against a
        # human reviewing this exact case in between the two passes.
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for transaction_id, review_count, original_exception_type in closed_candidates:
            submission = ReviewSubmission(
                reviewer_name="system:closed-loop-reverification",
                reviewer_role="analyst",
                decision="auto_closed",
                notes=f"Matcher re-run at {now}: the original exception "
                      f"('{original_exception_type}') is gone and the transaction is now fully "
                      f"clean -- not merely reclassified as a different, still-technically-"
                      f"auto-resolvable exception (e.g. a delayed bank posting has since arrived).",
                expected_review_count=review_count,
            )
            try:
                submit_review(transaction_id, submission)
                closed.append(transaction_id)
            except HTTPException as e:
                skipped.append({"transaction_id": transaction_id, "reason": str(e.detail)})
    else:
        closed = [transaction_id for transaction_id, _, _ in closed_candidates]

    return {
        "checked": len(closed_candidates) + len(changed_exception) + len(still_open),
        "closed": closed,
        "changed_exception": changed_exception,
        "still_open": still_open,
        "skipped": skipped,
        "dry_run": payload.dry_run,
    }


@app.get("/api/audit-chain/verify")
def audit_chain_verify():
    """Independently re-verifies the hash-chained audit trail (see
    review_backend/chain.py) -- walks the ENTIRE reviews table and
    recomputes every row's chain_hash from scratch, comparing against
    what's stored. Deliberately NOT cached: this is a small, cheap query
    (a handful of hashes over however many reviews exist at demo scale),
    and caching an integrity check would be exactly the wrong instinct --
    the whole point is that it re-derives the answer every time, not that
    it's fast."""
    conn = db.get_connection()
    try:
        return chain.verify_chain(conn)
    finally:
        conn.close()


@app.get("/api/run-summary")
def run_summary():
    """Serves whatever run_summary.py last wrote to
    CASH_POSITION_DATA_DIR/run_summary.txt -- deliberately NEVER triggers
    an LLM call on request, same "pre-computed, then served statically"
    pattern export_dashboard_data.py already uses for dashboard_data.json.
    Returns generated: False (not a 404) when the file doesn't exist yet --
    this is an optional convenience layer, a missing summary is a normal
    state for a dataset nobody's run `python run_summary.py` against yet,
    never an error."""
    path = os.path.join(CASH_POSITION_DATA_DIR, "run_summary.txt")
    if not os.path.exists(path):
        return {"generated": False, "summary": None}
    with open(path, encoding="utf-8") as f:
        return {"generated": True, "summary": f.read().strip()}


@app.post("/api/qa")
def qa(payload: QARequest):
    """Settlement Q&A agent (qa_agent/) -- direction #2 from the buildathon
    brief, additive to the existing reconciliation loop, not a replacement
    for anything. Genuinely different latency profile from every other
    endpoint in this file: a real tool-calling LLM round trip, ~1-2 minutes
    end to end on local Ollama, not a fast DB/matcher query. Deliberately
    NOT Redis-cached (see cache.py) -- every question is different, and a
    per-question cache key isn't worth the complexity for an endpoint
    that's asked interactively, not polled. A plain `def` (not `async def`)
    like every other endpoint here -- FastAPI already runs sync handlers in
    a threadpool, so one slow question never blocks /api/stats's 3-second
    poll or any other request.

    Builds a fresh ToolContext per call (same "computed fresh" pattern
    _cash_position_stats() used before caching existed) -- the ~1-2s
    matcher re-run is negligible next to the LLM round trip itself, so
    there's nothing worth caching here beyond what Ollama itself does."""
    try:
        report, settlement_matches, _ = run_matcher(CASH_POSITION_DATA_DIR)
        gateway, bank, _ = load_sources(CASH_POSITION_DATA_DIR)
        ctx = QAToolContext(report, gateway, bank, settlement_matches,
                             loan_book=load_loan_book(CASH_POSITION_DATA_DIR))
        client = OllamaToolClient(model=payload.model or qa_config.QA_MODEL)
        result = qa_ask(payload.question, ctx, client)
    except Exception as e:  # noqa: BLE001 -- Ollama down/unreachable is the
        # expected failure mode here, not a code bug; surfaced as a clear
        # 503 rather than a raw 500, same discipline as
        # reconciliation_statement()'s own error handling.
        raise HTTPException(503, f"Q&A agent unavailable: {type(e).__name__}: {e}")
    return result.model_dump()


@app.get("/api/stats")
def stats():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT transaction_id, amount_at_risk_rupees, gate_final_decision, "
            "investigation_gate_decision, matcher_exception_type, required_approval_tier, "
            "agent_confidence, investigated FROM cases"
        ).fetchall()
        counts = {"auto_resolved": 0, "pending": 0, "pending_manager_approval": 0,
                  "approved": 0, "overridden": 0, "escalated": 0, "auto_closed": 0}
        amount_by_status = {k: 0.0 for k in counts}
        by_exception_type: dict = {}
        by_tier = {1: 0, 2: 0}
        investigated_count = 0
        # One query for all latest review statuses, not one per case -- this
        # endpoint is polled every 3s by the frontend and deliberately isn't
        # Redis-cached (it must show live review state), so the old per-case
        # pattern meant 604 queries on every poll. See
        # _latest_review_status_by_txn for the measured 45x difference.
        latest_status = _latest_review_status_by_txn(conn)
        still_open = []
        for row in rows:
            s = _derive_status_from_latest(row, latest_status.get(row["transaction_id"]))
            counts[s] += 1
            amount_by_status[s] += row["amount_at_risk_rupees"]
            if s in OPEN_STATUSES:
                still_open.append(row["transaction_id"])

            et = row["matcher_exception_type"]
            bucket = by_exception_type.setdefault(
                et, {"count": 0, "amount_at_risk_rupees": 0.0, "investigated_count": 0})
            bucket["count"] += 1
            bucket["amount_at_risk_rupees"] += row["amount_at_risk_rupees"]
            if row["investigated"]:
                bucket["investigated_count"] += 1

            by_tier[row["required_approval_tier"]] += 1
            if row["investigated"]:
                investigated_count += 1

        exception_type_breakdown = [
            {"exception_type": et, "count": v["count"],
             "amount_at_risk_rupees": round(v["amount_at_risk_rupees"], 2),
             "investigated_count": v["investigated_count"]}
            for et, v in sorted(by_exception_type.items(), key=lambda kv: -kv[1]["count"])
        ]

        return {
            "total_cases": len(rows),
            "counts_by_status": counts,
            "amount_at_risk_rupees_by_status": {k: round(v, 2) for k, v in amount_by_status.items()},
            "exception_type_breakdown": exception_type_breakdown,
            "counts_by_tier": {"1": by_tier[1], "2": by_tier[2]},
            "investigated_count": investigated_count,
            # Regulatory SLA position across everything still awaiting a
            # human -- RBI's T+5 TAT bound and the Rs.100/day compensation
            # that accrues past it (see review_backend/sla.py). Computed
            # from the ledger's own business-day-aware expected settlement
            # date, not an invented internal target.
            "sla": sla.sla_summary(still_open, DEFAULT_AS_OF, CASH_POSITION_DATA_DIR),
            # Operational cycle time per review-queue status (how long a
            # case actually spends waiting at each stage, and which stage
            # is the current bottleneck) -- distinct from and complementary
            # to sla above (regulatory deadline vs. process throughput);
            # see review_backend/cycle_time.py's own docstring for why this
            # one is measured against real wall-clock time.
            "cycle_time": cycle_time.cycle_time_summary(conn),
            "cash_position": _cash_position_stats(),
            # Lets the frontend show a "LIVE SIMULATION" indicator without
            # needing a second build -- this server process and the main
            # demo's are the exact same code, just pointed at a different
            # database via REVIEW_QUEUE_DATABASE_URL and flagged via this
            # separate env var (see run_stream_simulator.py). A dedicated
            # flag rather than inspecting the database name/URL directly --
            # decouples "which demo is this" from "what the database
            # happens to be called," one less thing to keep in sync.
            "stream_mode": os.environ.get("REVIEW_QUEUE_MODE") == "stream",
        }
    finally:
        conn.close()


@app.get("/api/reconciliation-statement")
def reconciliation_statement():
    """The classic bank-rec bridge, served through the same short-TTL,
    best-effort Redis cache as _cash_position_stats() above (see
    review_backend/cache.py). This endpoint's own 30s client-side
    staleTime (useReconciliationStatement,
    ui/review-queue-app/src/hooks/useQueries.ts) only protects one browser
    tab's own repeated panel-expands; it does nothing for two different
    tabs, or a tab refresh, each triggering an independent fresh matcher
    run. Raises a clear 503 rather than a bare 500 if the underlying data
    can't be scored right now -- unchanged from before caching: a real
    failure inside the computation propagates through the cache helper
    completely untouched (only a successful result is ever cached), so
    this endpoint's error behavior is identical whether Redis is up,
    down, or never started."""
    def _compute():
        report, settlement_matches, _ = run_matcher(CASH_POSITION_DATA_DIR)
        gateway, bank, _ = load_sources(CASH_POSITION_DATA_DIR)
        return build_reconciliation_statement(report, gateway, bank, settlement_matches, DEFAULT_AS_OF)

    try:
        key = cache.reconciliation_statement_key(CASH_POSITION_DATA_DIR, DEFAULT_AS_OF.isoformat())
        return cache.cached_or_compute(key, RECONCILIATION_STATEMENT_CACHE_TTL_SECONDS, _compute)
    except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
        raise HTTPException(503, f"reconciliation statement unavailable: {type(e).__name__}: {e}")


@app.get("/")
def root():
    return RedirectResponse(url="/review-queue/")


# The review queue is now the React/Tailwind app in ui/review-queue-app/
# (built to dist/) -- mounted at its own sub-path so it can't collide with
# /api/* or the still-static ui/ files (showcase.html etc, which stay
# vanilla HTML/CSS/JS deliberately -- that page is meant to be hosted
# separately on GitHub Pages with no backend, so it can't depend on
# anything server-rendered or API-fed). Registration ORDER matters here
# exactly like it did before: both mounts must come after every /api/*
# route above, or FastAPI's routing precedence would let a static 404
# shadow a real API endpoint.
REACT_APP_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui",
                               "review-queue-app", "dist")
app.mount("/review-queue", StaticFiles(directory=REACT_APP_DIST, html=True), name="review-queue-app")
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
