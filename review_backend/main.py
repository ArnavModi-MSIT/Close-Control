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
from .config import MANAGER_APPROVAL_THRESHOLD_RUPEES
from .models import ReviewSubmission, ReverificationRequest
from .state_machine import next_status, InvalidTransition, APPLICATION_VERSION

from run_matcher import run as run_matcher
from matching.loaders import load_sources
from cash_position.engine import build_cash_position
from cash_position.config import DEFAULT_AS_OF
from cash_position.reconciliation_statement import build_reconciliation_statement

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
            automation_rate = float(
                (report["final_exception_type"].isna() | report["auto_resolve_eligible"]).mean()
            )
            return {
                "as_of": DEFAULT_AS_OF.isoformat(),
                "confirmed_rupees": round(snap["confirmed_rupees"], 2),
                "in_transit_rupees": round(snap["in_transit_rupees"], 2),
                "at_risk_rupees": round(snap["held_rupees"] + snap["at_risk_due_nominal_rupees"], 2),
                "projected_cash_position_rupees": round(snap["projected_cash_position_rupees"], 2),
                "automation_rate_pct": round(automation_rate * 100, 1),
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


def _derive_status(case_row, reviews) -> str:
    if reviews:
        return reviews[-1]["resulting_status"]
    # No human action yet -- the case's initial status depends on what the
    # gate itself decided. Prefer the investigation's own gate outcome when
    # one exists (it's the richer, later signal -- see db.py's migration
    # comment for investigation_gate_decision); fall back to the frozen
    # original proposal's outcome otherwise.
    decision = case_row["investigation_gate_decision"] or case_row["gate_final_decision"]
    return "auto_resolved" if decision == "auto_resolve" else "pending"


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

        cases = []
        for row in rows:
            reviews = _get_reviews(conn, row["transaction_id"])
            case_status = _derive_status(row, reviews)
            if status and case_status != status:
                continue
            cd = _row_to_case_dict(row)
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
            "review_history": [_row_to_review_dict(r) for r in reviews],
            "activity": _build_activity(cd, reviews),
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
        conn.execute(
            """INSERT INTO reviews
               (review_uuid, transaction_id, reviewer_name, reviewer_role, decision,
                override_field, override_old_value, override_new_value, notes,
                previous_status, resulting_status, created_at, application_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (review_uuid, transaction_id, submission.reviewer_name, submission.reviewer_role,
             submission.decision, submission.override_field, submission.override_old_value,
             submission.override_new_value, submission.notes, current_status, new_status,
             created_at, APPLICATION_VERSION),
        )
        conn.commit()

        return {
            "review_uuid": review_uuid,
            "transaction_id": transaction_id,
            "new_status": new_status,
            "created_at": created_at,
        }
    finally:
        conn.close()


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
        rows = conn.execute("SELECT transaction_id, matcher_exception_type FROM cases").fetchall()
        closed_candidates, changed_exception, still_open = [], [], []
        for row in rows:
            transaction_id = row["transaction_id"]
            case_row = _get_case_row(conn, transaction_id)
            reviews = _get_reviews(conn, transaction_id)
            status = _derive_status(case_row, reviews)
            if status not in ("pending", "pending_manager_approval"):
                continue  # already decided (by a human or the gate) -- not eligible for auto-closure

            if transaction_id not in is_clean_by_txn.index:
                still_open.append(transaction_id)  # can't currently observe it -- leave alone, not "clean"
            elif is_clean_by_txn[transaction_id]:
                closed_candidates.append((transaction_id, len(reviews), row["matcher_exception_type"]))
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
        for row in rows:
            reviews = _get_reviews(conn, row["transaction_id"])
            s = _derive_status(row, reviews)
            counts[s] += 1
            amount_by_status[s] += row["amount_at_risk_rupees"]

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
