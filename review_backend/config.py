"""Review-queue configuration -- kept separate from main.py so scripts that
just need a constant (seed_review_queue.py) don't have to import and
construct the whole FastAPI app to get it."""

# Second, higher boundary than agent/config.py's AUTO_RESOLVE_RISK_CEILING_RUPEES
# (which governs whether the SYSTEM may auto-resolve at all). This one
# governs whether a human approval needs a second, manager-level sign-off.
# Placeholder value -- tune against the real amount-at-risk distribution.
MANAGER_APPROVAL_THRESHOLD_RUPEES = 50000.00
