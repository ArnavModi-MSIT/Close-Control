"""Operational cycle-time / bottleneck tracking across the review queue.

Distinct from sla.py, and complementary to it. SLA measures a case's age
against RBI's fixed regulatory T+5 deadline (anchored to the LEDGER's own
expected_settlement_date, "as of" pinned to the dataset's own static
timeline -- see sla.py's own docstring for why). This module answers a
different question: how long does a case actually spend in each REVIEW
-QUEUE STATUS before moving to the next one, or -- for a case still
open -- how long has it been sitting in its current status so far. That
matters even for a case comfortably inside its SLA deadline: a case can
be fully within its regulatory bound while this still reveals that, say,
tier-2 cases spend most of their time stuck waiting specifically on the
SECOND (manager) approval, not the first. Idea adapted from Trintech's
"Close Progress & Bottleneck Tracking" (cycle times, delay identification)
-- checked against this project's existing code first: sla.py only
answers "is this case past its regulatory deadline," nothing here already
computed per-stage duration.

Deliberately measured against real WALL-CLOCK time (dt.datetime.now()),
NOT cash_position.config.DEFAULT_AS_OF -- the one place in review_backend/
that does, worth being explicit about given sla.py's opposite convention
is the established norm elsewhere in this app. That's not an
inconsistency: sla.py ages a case against the dataset's own synthetic July
2026 timeline (using wall-clock "now" there would mark a static historical
month falsely breached). Cycle time measures something different in
kind -- how long a real Postgres row has actually sat since it was really
inserted or last reviewed, in real time -- which genuinely is wall-clock
elapsed time, correctly, regardless of what date the underlying synthetic
transaction carries.

Reads only -- computes nothing that touches a case's status, gate
decision, or any auto-resolve/escalate outcome. An operational signal for
a human, same boundary sla.py itself is built on.
"""

import datetime as dt

# The two statuses a human can actually still act on -- the "bottleneck"
# headline only ever points here, never at a terminal status (an old
# `approved` case sitting untouched for months isn't a bottleneck, it's
# just done). Centralized in state_machine.py (was its own copy here,
# found duplicated in four more places in main.py by an external review
# pass) -- imported, not redefined, so this can't silently drift from the
# other callers.
from .state_machine import OPEN_STATUSES, TERMINAL_STATUSES


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(ts: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(ts)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _stage_stats(days_list: list) -> dict | None:
    """None (not zeros) when there's no data yet -- a stage nobody has
    ever transitioned OUT of has no measured duration, which is a
    genuinely different fact from "it takes 0 days," and the API/UI must
    say so rather than silently rendering a misleading zero."""
    if not days_list:
        return None
    s = sorted(days_list)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"count": n, "mean_days": round(sum(s) / n, 2), "median_days": round(median, 2)}


def cycle_time_summary(conn) -> dict:
    """Portfolio-level cycle time across every seeded case. Exactly two
    queries regardless of case count (all cases, all reviews, joined in
    Python) -- the same N+1 this project already found and fixed once on
    /api/stats/api/cases (see main.py's _latest_review_status_by_txn(),
    45x measured difference); this reuses that lesson rather than
    reintroducing the pattern for a new endpoint.
    """
    now = _now()
    cases = conn.execute(
        "SELECT transaction_id, seeded_at, investigation_gate_decision, gate_final_decision FROM cases"
    ).fetchall()
    reviews = conn.execute(
        "SELECT transaction_id, resulting_status, created_at FROM reviews ORDER BY transaction_id, id ASC"
    ).fetchall()

    reviews_by_txn: dict = {}
    for r in reviews:
        reviews_by_txn.setdefault(r["transaction_id"], []).append(r)

    # {status: [completed stage durations, in days]}
    completed_by_status: dict = {}
    # {status: [(transaction_id, days_in_status_so_far), ...]} -- right-censored,
    # still accruing.
    open_by_status: dict = {}

    for case in cases:
        # Same derivation _derive_status_from_latest() uses for a case
        # with no reviews yet -- the investigation's own gate outcome
        # wins over the frozen primary proposal's, matching main.py's
        # documented "richer, later signal" rule exactly (see the
        # auto_resolved 6->8 finding earlier this session for why that
        # matters in practice, not just in theory).
        decision = case["investigation_gate_decision"] or case["gate_final_decision"]
        current_status = "auto_resolved" if decision == "auto_resolve" else "pending"
        entered_at = _parse(case["seeded_at"])

        for r in reviews_by_txn.get(case["transaction_id"], []):
            left_at = _parse(r["created_at"])
            days = (left_at - entered_at).total_seconds() / 86400
            completed_by_status.setdefault(current_status, []).append(days)
            current_status = r["resulting_status"]
            entered_at = left_at

        days_open = (now - entered_at).total_seconds() / 86400
        open_by_status.setdefault(current_status, []).append((case["transaction_id"], days_open))

    by_status = {}
    for status in set(completed_by_status) | set(open_by_status):
        # currently_open_* only means what its name says for a status a case
        # can still be ACTED on from (OPEN_STATUSES) or auto_resolved (a
        # third, distinct, non-terminal category -- see state_machine.py --
        # a case can sit there indefinitely awaiting an optional human
        # revert). For a genuinely TERMINAL status (approved/overridden/
        # escalated/auto_closed), open_by_status[status] is really "time
        # since the terminal decision was made," not a queue wait -- a real,
        # different fact the field names strongly imply is the former.
        # Rather than populate a misleadingly-named field (or a fabricated
        # 0 that would misreport as "checked, found zero waiting"), those
        # statuses are simply not included here -- same "None/absent, not a
        # fake zero" discipline _stage_stats() already applies one level up.
        # Found via external review: "cases currently open in 'approved',
        # avg 12 days" reads as a real backlog problem it isn't.
        if status in TERMINAL_STATUSES:
            continue
        open_list = open_by_status.get(status, [])
        open_days = [d for _, d in open_list]
        oldest = max(open_list, key=lambda x: x[1]) if open_list else None
        by_status[status] = {
            "completed": _stage_stats(completed_by_status.get(status, [])),
            "currently_open_count": len(open_list),
            "currently_open_avg_days": round(sum(open_days) / len(open_days), 2) if open_days else None,
            "oldest_open_transaction_id": oldest[0] if oldest else None,
            "oldest_open_days": round(oldest[1], 2) if oldest else None,
        }

    bottleneck_status, bottleneck_avg = None, None
    for status in OPEN_STATUSES:
        avg = by_status.get(status, {}).get("currently_open_avg_days")
        if avg is not None and (bottleneck_avg is None or avg > bottleneck_avg):
            bottleneck_status, bottleneck_avg = status, avg

    return {
        "as_of": now.isoformat(),
        "by_status": by_status,
        "bottleneck_status": bottleneck_status,
        "bottleneck_avg_days": bottleneck_avg,
    }
