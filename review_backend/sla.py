"""Regulatory SLA tracking for open review cases, grounded in RBI's actual
Turn-Around-Time framework rather than an invented internal target.

RBI's "Harmonisation of Turn Around Time (TAT) and customer compensation
for failed transactions using authorised Payment Systems" circular
(20.09.2019) sets **T+5 business days** as the outer bound for resolving a
failed/short transaction, past which the bank owes the customer **Rs.100
per day** automatically -- suo moto, without the customer having to
complain. agent/policy_kb.py's POLICY-007 and POLICY-009 already cite this
as what "escalate" should mean operationally; this module is what makes the
review queue actually *act* on it instead of only quoting it.

Two deliberate design points:

1. The deadline is derived from the internal ledger's own
   `expected_settlement_date`, which data_generation/sources/ledger.py
   already computed as add_business_days(captured_at, 2) -- i.e. it is
   genuinely T+2 and genuinely business-day aware. T+5 is therefore that
   date plus 3 more business days, not "+3 calendar days" and not a second
   independent guess at the settlement date.

2. Aging is measured against cash_position.config.DEFAULT_AS_OF, the same
   reference date every other money figure in this app uses -- NOT the real
   wall clock. The dataset is a fixed historical month; aging it against
   today would mark every case breached and mean nothing. Using the shared
   as-of keeps the SLA panel internally consistent with the cash-position
   numbers shown beside it (see CLAUDE.md's known-limitations note on
   as_of under the stream simulator).

Nothing here changes a case's status or the gate's decision -- an SLA
breach is an operational priority signal for a human, never an
authorization to act. Same boundary as everywhere else in this project.
"""

import datetime as dt
from functools import lru_cache

# RBI TAT circular (20.09.2019): outer bound for resolution, and the
# automatic per-day compensation owed past it.
RBI_TAT_BUSINESS_DAYS = 5
RBI_COMPENSATION_PER_DAY_RUPEES = 100.0

# The ledger's expected_settlement_date is already T+2 business days from
# capture, so the T+5 deadline is 3 further business days on top of it.
_LEDGER_EXPECTED_IS_T_PLUS = 2
_BUSINESS_DAYS_FROM_EXPECTED_TO_DEADLINE = RBI_TAT_BUSINESS_DAYS - _LEDGER_EXPECTED_IS_T_PLUS


def _is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5


def add_business_days(d: dt.date, n: int) -> dt.date:
    """Same rule data_generation/utils.py uses. Reimplemented locally rather
    than imported so review_backend/ doesn't take a dependency on the data
    generator (which is a build-time concern, not a runtime one)."""
    cur, added = d, 0
    while added < n:
        cur += dt.timedelta(days=1)
        if _is_business_day(cur):
            added += 1
    return cur


def business_days_between(start: dt.date, end: dt.date) -> int:
    """Business days strictly after `start` up to and including `end`.
    Zero when end <= start (i.e. not yet past the deadline)."""
    if end <= start:
        return 0
    days, cur = 0, start
    while cur < end:
        cur += dt.timedelta(days=1)
        if _is_business_day(cur):
            days += 1
    return days


@lru_cache(maxsize=4)
def deadlines_for(data_dir: str) -> dict:
    """{transaction_id: RBI T+5 deadline date}, built once per data_dir.

    Cached because the ledger is a static file for the main demo; the
    stream simulator points at its own data_dir and so gets its own entry
    (hence maxsize>1). Call deadlines_for.cache_clear() if a data_dir's
    ledger is rewritten in-process."""
    from matching.loaders import load_sources
    _, _, ledger = load_sources(data_dir)
    out = {}
    for row in ledger.itertuples(index=False):
        expected = row.expected_settlement_date
        if isinstance(expected, str):
            expected = dt.date.fromisoformat(expected)
        out[row.transaction_id] = add_business_days(
            expected, _BUSINESS_DAYS_FROM_EXPECTED_TO_DEADLINE)
    return out


def sla_for_case(transaction_id: str, as_of: dt.date, data_dir: str) -> dict:
    """SLA position for one case. Returns None-ish fields (not an error) for
    a transaction with no ledger row -- e.g. a synthetic test case -- so an
    endpoint never fails just because SLA can't be computed for one row."""
    deadline = deadlines_for(data_dir).get(transaction_id)
    if deadline is None:
        return {"sla_deadline": None, "sla_days_overdue": 0, "sla_breached": False,
                "sla_compensation_accrued_rupees": 0.0}
    overdue = business_days_between(deadline, as_of)
    return {
        "sla_deadline": deadline.isoformat(),
        "sla_days_overdue": overdue,
        "sla_breached": overdue > 0,
        # Rs.100/day is what RBI's circular says accrues automatically past
        # T+5. Reported as exposure a human should see, never auto-actioned.
        "sla_compensation_accrued_rupees": round(overdue * RBI_COMPENSATION_PER_DAY_RUPEES, 2),
    }


def sla_summary(open_transaction_ids, as_of: dt.date, data_dir: str) -> dict:
    """Portfolio-level SLA position across the still-open cases."""
    deadlines = deadlines_for(data_dir)
    breached, total_days, worst = 0, 0, None
    for txn_id in open_transaction_ids:
        deadline = deadlines.get(txn_id)
        if deadline is None:
            continue
        overdue = business_days_between(deadline, as_of)
        if overdue > 0:
            breached += 1
            total_days += overdue
            if worst is None or overdue > worst[1]:
                worst = (txn_id, overdue)
    return {
        "as_of": as_of.isoformat(),
        "tat_business_days": RBI_TAT_BUSINESS_DAYS,
        "open_cases_checked": len(list(open_transaction_ids)) if not hasattr(
            open_transaction_ids, "__len__") else len(open_transaction_ids),
        "breached_count": breached,
        "total_days_overdue": total_days,
        "compensation_exposure_rupees": round(total_days * RBI_COMPENSATION_PER_DAY_RUPEES, 2),
        "worst_case_transaction_id": worst[0] if worst else None,
        "worst_case_days_overdue": worst[1] if worst else 0,
    }
