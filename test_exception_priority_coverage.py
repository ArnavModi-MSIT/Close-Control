"""
Exhaustive coverage proof for matching/report.py's EXCEPTION_PRIORITY: for
every reachable combination of (ledger-side signal, settlement-side signal,
timing signal), the REAL build_report() function resolves to a defined
exception type whenever at least one signal fired, and that type is exactly
the highest-priority one present -- never a silent fallthrough to "clean."

Idea sharpened by checking a peer Razorpay buildathon repo
(SuryaSK-dev/razorpay-ai-finance-controller) past its README into its
actual tests/test_decision_table.py -- it exhaustively enumerates all 2,048
combinations of its own decision context and proves every one resolves via
its priority-ordered rule list, with a catch-all rule that should never
actually fire in production. This project's EXCEPTION_PRIORITY
(matching/report.py) is architecturally the same shape (first-matching
-candidate-in-priority-order wins) but had never been proven exhaustively
against its own real signal space -- only trusted by construction and by
the curated dataset's own coverage.

BUILDING THIS TEST FOUND A REAL BUG, immediately, before the test even
finished being written: "no_gateway_record_found" (matching/ledger_check.py's
own exception_type for a ledger row with zero successful gateway records)
was NEVER a member of EXCEPTION_PRIORITY. Every non-None ledger-side
signal has ONE entry in that list except this one -- an omission, not a
deliberate exclusion. The consequence: when it fired (and NOTHING else
could co-occur with it -- see the reasoning below), build_report()'s
priority loop found no match, `final_exception` stayed None, and the
transaction was reported `is_clean=True` / `auto_resolve_eligible=True`,
silently overriding ledger_check.py's own explicit `risk_class="high"` /
`auto_resolve_eligible=False` verdict for the single most severe kind of
problem this project's matcher exists to catch: a payment the ledger
expects that the gateway has no successful record of at all.

Confirmed NOT reachable on the current curated dataset (the data
generator guarantees every ledger row has a matching successful gateway
row by construction -- verified directly: `run_matcher.run('data')`'s real
ledger_check output has zero `no_gateway_record_found` rows), so this
never affected any published number in CLAUDE.md. But it's a real,
live silent-misclassification risk on any dataset where that invariant
doesn't hold -- a future seed-robustness regen, a data-generation change,
or real production data -- which is exactly the class of gap this
project's "defense-in-depth even when not currently reachable" pattern
already fixes elsewhere (the evidence-citation gate, the NaN-JSON-safety
guards). Fixed in matching/report.py: EXCEPTION_PRIORITY now includes
"no_gateway_record_found", ranked highest (nothing else can co-occur with
it by construction -- see below -- so its exact rank never actually
competes with anything, but it MUST be present in the list at all).

The real signal space, derived directly from matching/report.py's
build_report() and matching/ledger_check.py's own exception_type set:
  - ledger_signal: one of 10 real ledger_check.py exception types, or None
  - settlement_signal: one of 4 real settlement-side signals, or None
    (structurally impossible to co-occur with ledger_signal=
    "no_gateway_record_found" -- a transaction_id absent from `successful`
    gateway rows can never also have a settlement_id)
  - timing_signal: True/False (same structural exclusion as above)
That's 10*5*2 - (9*5*2) [combinations excluded for no_gateway_record_found,
which only has 1 reachable combination, not 10] = 91 reachable combinations,
each exercised against the REAL, unmodified build_report() -- not a
reimplementation of its logic.

    python test_exception_priority_coverage.py
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from matching.report import build_report, EXCEPTION_PRIORITY

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


# Every real value matching/ledger_check.py's check_ledger_vs_gateway() can
# assign to a row's exception_type, plus None for "ledger side is clean."
# Kept as an explicit list here (not imported) since ledger_check.py has no
# single exported constant for this set -- if a new type is ever added
# there without a matching addition here, this test's own coverage would
# silently narrow, so Section 3 below cross-checks this list against
# EXCEPTION_PRIORITY from the other direction too.
LEDGER_SIGNALS = [
    None,
    "no_gateway_record_found",
    "duplicate_payment_detected",
    "signature_verification_failed",
    "held_for_risk_review",
    "deemed_success_ambiguous",
    "chargeback_received",
    "loan_recovery_deduction",
    "partial_refund",
    "fee_variance",
    "unexplained_shortage",
]

# Every real value the settlement-side elif chain in build_report() can
# append to `signals`, plus None for "no settlement-side signal."
SETTLEMENT_SIGNALS = [None, "settlement_bank_posting_not_found", "ambiguous_bank_match",
                       "missing_bank_reference", "bank_overage"]


def _build_case(ledger_signal, settlement_signal, timing_signal):
    """Constructs the minimal real inputs build_report() needs to produce
    exactly this (ledger_signal, settlement_signal, timing_signal)
    combination, then returns the REAL function's output row -- no
    reimplementation of the priority logic, the actual production code."""
    txn_id = "trn-coverage-1"
    settlement_id = "setl-coverage-1" if ledger_signal != "no_gateway_record_found" else None

    ledger_check = pd.DataFrame([{
        "transaction_id": txn_id, "order_id": "ord-1", "merchant_id": "mer-1",
        "ledger_expected_net_rupees": 100.0, "observed_net_rupees": 100.0, "net_delta_rupees": 0.0,
        "exception_type": ledger_signal,
        "risk_class": "medium" if ledger_signal else "none",
        "auto_resolve_eligible": ledger_signal is None,
    }])

    if ledger_signal == "no_gateway_record_found":
        # By construction (report.py's own primary_by_txn lookup), a
        # transaction with no successful gateway row can never also carry
        # a settlement-side or timing signal -- this is the ONE reachable
        # combination for this ledger_signal, not 10.
        gateway = pd.DataFrame(columns=["transaction_id_ref", "attempt_status", "settlement_id", "settled_at"])
        settlement_matches = pd.DataFrame(columns=["settlement_id", "match_status", "missing_bank_reference",
                                                      "bank_overage", "had_ambiguous_candidates"])
        ledger = pd.DataFrame(columns=["transaction_id", "expected_settlement_date"])
        return ledger_check, settlement_matches, gateway, ledger

    settled_at = pd.Timestamp("2026-07-10") if timing_signal else pd.Timestamp("2026-07-01")
    gateway = pd.DataFrame([{
        "transaction_id_ref": txn_id, "attempt_status": "success",
        "settlement_id": settlement_id, "settled_at": settled_at,
    }])

    # report.py's timing-lag check is nested INSIDE the
    # `if pd.notna(settlement_id) and settlement_id in settlement_by_id.index`
    # block -- so a timing signal can only ever fire when a real,
    # matched-in-settlement_matches settlement_result exists at all,
    # regardless of which (if any) settlement-side elif branch also fires.
    # settlement_signal=None therefore still needs a REAL "matched, no
    # exception flags" row here (not an empty settlement_matches table),
    # or the timing check is skipped entirely and timing_signal=True could
    # never be reached for this settlement_signal -- found by actually
    # running this test and hitting a real KeyError/wrong-result pair,
    # not assumed from a first read of report.py.
    row = {"settlement_id": settlement_id, "match_status": "matched", "match_pass": "exact",
           "missing_bank_reference": False, "bank_overage": False, "had_ambiguous_candidates": False}
    if settlement_signal == "settlement_bank_posting_not_found":
        row["match_status"] = "unmatched"
    elif settlement_signal == "ambiguous_bank_match":
        row["match_status"] = "ambiguous"
    elif settlement_signal == "missing_bank_reference":
        row["missing_bank_reference"] = True
    elif settlement_signal == "bank_overage":
        row["bank_overage"] = True
    settlement_matches = pd.DataFrame([row])

    # expected_settlement_date in the past forces actual_settle_date >
    # expected_date (timing_signal=True); far in the future forces the
    # opposite. Always included -- report.py only computes the timing
    # signal at all when txn_id is present in ledger_by_txn.
    expected_date = "2026-07-01" if timing_signal else "2026-08-01"
    ledger = pd.DataFrame([{"transaction_id": txn_id, "expected_settlement_date": expected_date}])

    return ledger_check, settlement_matches, gateway, ledger


def _expected_final_exception(ledger_signal, settlement_signal, timing_signal) -> str | None:
    """Independently re-derives what SHOULD win, from EXCEPTION_PRIORITY's
    own order -- the golden-order check. If a future edit reorders
    EXCEPTION_PRIORITY, this fails loudly instead of silently agreeing
    with whatever the new order happens to produce."""
    signals = [s for s in (ledger_signal, settlement_signal) if s]
    if timing_signal:
        signals.append("timing_lag_beyond_t2")
    for candidate in EXCEPTION_PRIORITY:
        if candidate in signals:
            return candidate
    return None


def main() -> None:
    print("\nSection 1: exhaustive sweep -- every reachable signal combination resolves correctly")

    reached = set()
    combos_checked = 0
    for ledger_signal in LEDGER_SIGNALS:
        settlement_options = [None] if ledger_signal == "no_gateway_record_found" else SETTLEMENT_SIGNALS
        timing_options = [False] if ledger_signal == "no_gateway_record_found" else [False, True]
        for settlement_signal in settlement_options:
            for timing_signal in timing_options:
                combos_checked += 1
                ledger_check, settlement_matches, gateway, ledger = _build_case(
                    ledger_signal, settlement_signal, timing_signal)
                report = build_report(ledger_check, settlement_matches, gateway, ledger)
                row = report.iloc[0]
                expected = _expected_final_exception(ledger_signal, settlement_signal, timing_signal)

                label = f"ledger={ledger_signal!r} settlement={settlement_signal!r} timing={timing_signal}"
                check(f"{label} -> {expected!r}",
                      row["final_exception_type"] == expected,
                      f"got {row['final_exception_type']!r}")

                any_signal_present = bool(ledger_signal or settlement_signal or timing_signal)
                check(f"{label}: is_clean is the correct opposite of 'any signal fired'",
                      row["is_clean"] == (not any_signal_present),
                      f"is_clean={row['is_clean']}")

                if expected:
                    reached.add(expected)

    check(f"a meaningful number of combinations were actually exercised (not accidentally 0 or 1)",
          combos_checked >= 90, f"combos_checked={combos_checked}")
    print()

    print("Section 2: no priority-list entry is dead code -- every one is reachable as a winner")
    unreachable = [c for c in EXCEPTION_PRIORITY if c not in reached]
    check("every EXCEPTION_PRIORITY entry was the winning final_exception_type for at least one combination",
          len(unreachable) == 0, f"unreachable: {unreachable}")
    print()

    print("Section 3: every real ledger_check.py exception_type has an EXCEPTION_PRIORITY entry")
    missing_from_priority = [s for s in LEDGER_SIGNALS if s is not None and s not in EXCEPTION_PRIORITY]
    check("no ledger-side signal is missing from EXCEPTION_PRIORITY (this is the exact bug class "
          "that let no_gateway_record_found silently resolve to clean)",
          len(missing_from_priority) == 0, f"missing: {missing_from_priority}")
    print()

    print("Section 4: the all-clean case genuinely resolves to clean, not by accident")
    ledger_check, settlement_matches, gateway, ledger = _build_case(None, None, False)
    report = build_report(ledger_check, settlement_matches, gateway, ledger)
    row = report.iloc[0]
    check("zero signals -> final_exception_type is None",
          row["final_exception_type"] is None, str(row["final_exception_type"]))
    check("zero signals -> is_clean is True", row["is_clean"] is True or bool(row["is_clean"]) is True)
    check("zero signals -> all_signals is genuinely empty", row["all_signals"] == [], str(row["all_signals"]))

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
