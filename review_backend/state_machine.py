"""Explicit state-transition machine for review-queue cases.

Added in direct response to a design review that found the original
"derive status from most recent decision" approach ambiguous (e.g.
analyst-approve -> manager-approve -> analyst-escalate -> manager-approve
-- what status is that?). Nothing about a case's status is inferred
implicitly here; every legal transition is listed below, and everything
else is rejected with a clear reason.

States: [auto_resolved ->] pending -> [pending_manager_approval ->] approved | overridden | escalated

Deliberate, documented choice on reopening (the design review explicitly
asked for this to be explicit rather than accidental): only `escalated`
can ever act on an already-terminal case, and only to pile on more
context (still `escalated`, not a new state). `approved`/`overridden`
are hard-terminal -- re-approving or re-overriding a closed case is
rejected, not silently accepted.

`auto_resolved` is a case's INITIAL derived status (see
main.py:_derive_status) when the gate itself decided auto_resolve --
zero human input yet, by construction. It is not seeded from the review
queue's own decisions; it exists so a case the AI resolved on its own
still shows up for a human to see and, if they disagree, `reverted` back
to `pending` for the normal approve/override/escalate flow to take over.
This is the one and only transition available directly from
`auto_resolved` -- there is no "approve an auto-resolve," because it was
never pending approval in the first place; there is only "leave it be"
(no action) or "revert it" (explicit human disagreement, logged).
"""

APPLICATION_VERSION = "0.1.0"

VALID_ROLES = {"analyst", "manager"}
VALID_DECISIONS = {"approved", "overridden", "escalated", "reverted", "auto_closed"}
TERMINAL_STATUSES = {"approved", "overridden", "escalated", "auto_closed"}

# Fields on `cases` an override is allowed to reference. Not a suggestion --
# review_backend/main.py rejects anything outside this set before it ever
# reaches this module, per the design review's "do not allow arbitrary
# database-column mutation" finding.
OVERRIDABLE_FIELDS = {
    "agent_exception_type",
    "agent_root_cause",
    "agent_recommended_action",
    "agent_policy_id",
}


class InvalidTransition(Exception):
    def __init__(self, message: str, http_status: int = 422):
        super().__init__(message)
        self.http_status = http_status


def next_status(*, current_status: str, tier: int, decision: str, role: str,
                 reviewer_name: str, prior_analyst_reviewer: str | None) -> str:
    """Pure function: given a case's current derived status and one
    incoming review event, return the new status or raise
    InvalidTransition. Never touches the database -- callers persist the
    review row themselves, only after this returns successfully."""

    if decision not in VALID_DECISIONS:
        raise InvalidTransition(f"unknown decision {decision!r}", 400)
    if role not in VALID_ROLES:
        raise InvalidTransition(f"unknown reviewer_role {role!r}", 400)
    if tier not in (1, 2):
        raise InvalidTransition(f"unknown approval tier {tier!r}", 400)

    if decision == "reverted":
        if current_status != "auto_resolved":
            raise InvalidTransition(
                f"case is '{current_status}', not 'auto_resolved' -- only a case the AI "
                f"resolved on its own can be reverted; anything else already went through "
                f"(or is going through) the normal human review flow", 409)
        return "pending"

    if current_status == "auto_resolved" and decision != "reverted":
        raise InvalidTransition(
            "an auto-resolved case has no pending approval to act on -- revert it first "
            "if you disagree with the AI's decision, then approve/override/escalate normally", 409)

    if decision == "overridden":
        if current_status in TERMINAL_STATUSES:
            raise InvalidTransition(
                f"case is already '{current_status}' -- cannot override a closed case; "
                f"escalate it instead if it needs to be reopened", 409)
        return "overridden"

    if decision == "escalated":
        if current_status in ("approved", "overridden"):
            raise InvalidTransition(
                f"case is already '{current_status}' -- that's closed, not reopenable "
                f"via escalation", 409)
        return "escalated"  # from pending, pending_manager_approval, already escalated, or auto_closed

    if decision == "auto_closed":
        # The closed-loop re-verification job's own decision (see
        # review_backend/main.py's POST /api/reverify) -- the matcher no
        # longer detects this transaction as an exception, so it's closed
        # without a human touching it. Only legal while a case is still
        # awaiting a human decision; a human's already-made decision
        # (approved/overridden/escalated) is never silently overwritten by
        # an automated job, and an auto_resolved case is a different
        # mechanism entirely (blocked by the guard above already).
        if current_status not in ("pending", "pending_manager_approval"):
            raise InvalidTransition(
                f"case is '{current_status}' -- automated re-verification can only "
                f"close a case still awaiting human review, not one a human has "
                f"already acted on", 409)
        return "auto_closed"

    # decision == "approved"
    if current_status in TERMINAL_STATUSES:
        raise InvalidTransition(
            f"case is already '{current_status}' -- use escalate to reopen, not approve", 409)

    if tier == 1:
        if current_status != "pending":
            raise InvalidTransition(f"unexpected state '{current_status}' for a tier-1 case", 409)
        return "approved"

    # tier == 2
    if current_status == "pending":
        if role == "manager":
            raise InvalidTransition(
                "this tier-2 case needs an analyst approval before a manager can sign off "
                "-- a manager cannot satisfy the analyst prerequisite", 422)
        return "pending_manager_approval"

    if current_status == "pending_manager_approval":
        if role == "analyst":
            raise InvalidTransition(
                "this case already has its analyst approval and is awaiting a manager "
                "-- a second analyst approval doesn't change that", 422)
        if reviewer_name == prior_analyst_reviewer:
            raise InvalidTransition(
                "tier-2 approval requires a different person for the manager sign-off "
                "than the analyst who approved it", 422)
        return "approved"

    raise InvalidTransition(f"unexpected state '{current_status}' for a tier-2 case", 409)
