"""
Standalone proof for agent/run_summary.py's whole-run narrative summary.
See that module's own docstring for the full design (idea adapted from
Microsoft Copilot for Finance's "generative AI report summary" -- scoped
down to mock-first + optional Ollama, explicitly lower-stakes polish, not
a capability gap).

Proves:
  1. The mock template correctly cites every real number it's given --
     not just "produces some text," but the SPECIFIC figures.
  2. The mock path correctly identifies the largest cluster/amplification
     without ever computing anything itself (garbage in the summary would
     mean the CALLER's numbers were wrong, never this module's own math,
     since it does none).
  3. The Ollama path is genuinely wired -- captured via a monkeypatched
     requests.post, no live Ollama server needed, no network dependency,
     matching the same reasoning test_corrections.py gives for faking
     investigator/'s Ollama client rather than calling a real one.
  4. A failed Ollama call falls back to the mock template rather than
     raising -- a run summary is a convenience, never something worth
     breaking a demo over.

    python test_run_summary.py
"""

import sys
import types

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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


def _fake_report(n_clean: int, n_exceptions: int) -> pd.DataFrame:
    rows = [{"final_exception_type": None, "auto_resolve_eligible": True} for _ in range(n_clean)]
    rows += [{"final_exception_type": "missing_bank_reference", "auto_resolve_eligible": False}
             for _ in range(n_exceptions)]
    return pd.DataFrame(rows)


def main() -> None:
    from agent.run_summary import build_run_summary

    print("\nSection 1: mock narrative cites the real numbers it's given")
    report = _fake_report(n_clean=100, n_exceptions=20)
    root_cause_summary = {
        "escalated_cases": 20, "root_cause_clusters": 5, "amplification_factor": 4.0,
        "multi_case_clusters": 2, "cases_in_multi_case_clusters": 15,
        "pct_cases_in_multi_case_clusters": 75.0, "singleton_clusters": 3,
        "largest_cluster_case_count": 10, "largest_cluster_id": "rc-0000",
        "largest_cluster_exception_type": "missing_bank_reference",
        "total_amount_at_risk_rupees": 123456.78,
    }
    text = build_run_summary(report, root_cause_summary, provider="mock")
    check("cites the real total transaction count (120)", "120" in text, text)
    check("cites the real automated count (100, since 0 exceptions were auto-eligible here)",
          "100" in text, text)
    check("cites the real escalated count (20)", "20" in text, text)
    check("cites the real cluster count (5)", "5" in text, text)
    check("cites the real amplification factor (4.0x)", "4.0x" in text, text)
    check("cites the real largest-cluster exception type", "missing_bank_reference" in text, text)
    check("cites the real largest-cluster case count (10)", "10" in text, text)
    check("cites the real amount at risk", "1,23,456.78" in text or "123,456.78" in text, text)
    check("is labeled as the mock provider, not silently passed off as a real model",
          text.startswith("[MOCK PROVIDER"), text)
    print()

    print("Section 2: mock narrative degrades correctly with zero escalated cases")
    clean_report = _fake_report(n_clean=50, n_exceptions=0)
    empty_summary = {"escalated_cases": 0, "root_cause_clusters": 0, "amplification_factor": 0.0,
                      "multi_case_clusters": 0, "cases_in_multi_case_clusters": 0,
                      "pct_cases_in_multi_case_clusters": 0.0, "singleton_clusters": 0,
                      "largest_cluster_case_count": 0, "largest_cluster_id": None,
                      "largest_cluster_exception_type": None, "total_amount_at_risk_rupees": 0.0}
    text2 = build_run_summary(clean_report, empty_summary, provider="mock")
    check("a fully-clean run gets its own real sentence, not a division-by-zero crash "
          "or a nonsensical '0 root causes, 0.0x amplification' sentence",
          "50" in text2 and "reconciled cleanly" in text2, text2)
    print()

    print("Section 3: the Ollama path is genuinely wired (mocked request, no live server)")
    import agent.run_summary as run_summary_module

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "A real narrated summary from a fake Ollama."}}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse()

    real_post = run_summary_module.requests.post
    run_summary_module.requests.post = _fake_post
    try:
        text3 = build_run_summary(report, root_cause_summary, provider="ollama")
        check("ollama path returns the (fake) model's actual text, not the mock template",
              text3 == "A real narrated summary from a fake Ollama.", text3)
        check("the real facts (not placeholders) were sent in the request payload",
              "120" in captured["payload"]["messages"][0]["content"]
              and "missing_bank_reference" in captured["payload"]["messages"][0]["content"],
              str(captured.get("payload")))
        check("uses plain free-text completion, NOT format=json (this output is prose "
              "for a human, not a Pydantic-validated schema like the per-case agent uses)",
              "format" not in captured["payload"], str(captured["payload"]))

        print()
        print("Section 4: a failed Ollama call falls back to the mock template, never raises")

        def _failing_post(url, json, timeout):
            raise ConnectionError("Ollama not running")

        run_summary_module.requests.post = _failing_post
        text4 = build_run_summary(report, root_cause_summary, provider="ollama")
        check("falls back to a real, correct mock summary instead of raising",
              text4.startswith("[MOCK PROVIDER") and "120" in text4, text4)
    finally:
        run_summary_module.requests.post = real_post

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
