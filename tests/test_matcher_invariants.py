"""
Standalone tests for run_matcher.py's own invariant gate -- proves the
matcher's OWN output is re-verified on every real run(), not just when
evaluate.py happens to call matching/diagnostics.py's functions directly.

    python tests/test_matcher_invariants.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import run_matcher


def test_real_dataset_passes_the_invariant_gate_cleanly():
    """The actual, unmodified curated dataset must never trip this --
    matching/diagnostics.py's own numbers already proved zero double
    -consumed rows and zero non-tolerance exact/split deltas exist on it;
    this proves run() itself doesn't raise when it re-checks the same
    thing on every call, not just that the underlying functions agree in
    isolation."""
    report, settlement_matches, ledger_check = run_matcher.run()
    assert len(report) > 0
    print(f"PASS -- run() completed cleanly against the real dataset "
          f"({len(report)} transactions, {len(settlement_matches)} settlements)")


def test_a_real_conservation_violation_is_caught():
    """Monkeypatches settlement_conservation_summary() (as run_matcher.py
    itself imported it into its own namespace) to return a fake finding --
    proving run() actually raises MatcherInvariantError on a genuine
    exact/split-pass conservation mismatch, not just that the check
    exists unreachably. Restores the real function afterward regardless
    of outcome, so this test can never leave the module patched for any
    other test that happens to run after it."""
    real_fn = run_matcher.settlement_conservation_summary

    def _fake_conservation_summary(settlement_matches):
        result = real_fn(settlement_matches)
        result = dict(result)
        result["exact_or_split_pass_with_real_delta"] = ["setl_FAKE_TAMPERED"]
        return result

    run_matcher.settlement_conservation_summary = _fake_conservation_summary
    try:
        raised = False
        try:
            run_matcher.run()
        except run_matcher.MatcherInvariantError as e:
            raised = True
            assert "setl_FAKE_TAMPERED" in str(e)
    finally:
        run_matcher.settlement_conservation_summary = real_fn

    assert raised, "run() did not raise MatcherInvariantError on a genuine conservation violation"
    print("PASS -- a real conservation violation (exact/split pass, real delta) raises MatcherInvariantError")

    # Restored correctly -- a second, real call must succeed cleanly again.
    report, _, _ = run_matcher.run()
    assert len(report) > 0
    print("PASS -- the real function is fully restored; a subsequent real run() succeeds again")


ALL_TESTS = [
    test_real_dataset_passes_the_invariant_gate_cleanly,
    test_a_real_conservation_violation_is_caught,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        print(f"{t.__name__}:")
        t()
        print()
    print(f"All {len(ALL_TESTS)} matcher-invariant tests passed.")
