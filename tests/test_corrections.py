"""
Standalone proof for corrections.py's correction-memory feature: past
human overrides of the AI's classification, surfaced back into FUTURE
prompts as a few-shot example. See corrections.py's own docstring for the
full design (idea adapted from HighRadius's "learns from patterns and
corrections over time" claim -- checked against this project's code
first, nothing like it existed before this).

This proves three things, each load-bearing on its own:
  1. corrections.py's pure functions (append/load/format) behave correctly
     in isolation -- including the parts that are easy to get subtly wrong
     (MAX_CORRECTIONS_PER_TYPE truncation, cross-type isolation, tolerance
     for a missing file).
  2. agent/client.py's resolve_exception() actually threads the correction
     block into the real system_prompt sent to a provider -- captured via
     a fake provider, not assumed from reading the source.
  3. investigator/loop.py's investigate() does the same, captured via a
     fake OllamaToolClient that ends the tool-round loop immediately (no
     real Ollama call, no network dependency, no multi-second wait) --
     same reasoning CLAUDE.md gives for always running the real
     investigator in the background: this test suite must stay fast.

Everything runs against an isolated temp data_dir -- never data/'s real
correction_log.jsonl (if the live demo has ever written one).

    python tests/test_corrections.py
"""

import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = _os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

import os
import sys
import shutil
import tempfile

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


def main() -> None:
    tmp_dir = tempfile.mkdtemp(prefix="corrections_test_")
    try:
        import corrections

        print("\nSection 1: pure functions in isolation")

        check("load_corrections() on a missing file returns {}, not an error",
              corrections.load_corrections(tmp_dir) == {})
        check("correction_block_for() on a missing file returns '' ",
              corrections.correction_block_for("missing_bank_reference", tmp_dir) == "")

        corrections.append_correction(
            tmp_dir, transaction_id="trn-c1", matcher_exception_type="missing_bank_reference",
            override_field="agent_policy_id", override_old_value="POLICY-004",
            override_new_value="POLICY-007", reason="matcher's type maps to POLICY-007",
            reviewer_name="ana", created_at="2026-08-01T00:00:00+00:00",
        )
        block = corrections.correction_block_for("missing_bank_reference", tmp_dir)
        check("after one real correction, the block is non-empty", block != "")
        check("the block names the overridden field", "agent_policy_id" in block)
        check("the block carries the old and new values", "POLICY-004" in block and "POLICY-007" in block)
        check("the block carries the reviewer's own stated reason",
              "matcher's type maps to POLICY-007" in block)

        check("a DIFFERENT exception_type sees no correction (no cross-type leakage)",
              corrections.correction_block_for("partial_refund", tmp_dir) == "")

        corrections.append_correction(
            tmp_dir, transaction_id="trn-c2", matcher_exception_type="missing_bank_reference",
            override_field="agent_recommended_action", override_old_value="Escalate",
            override_new_value="Escalate to treasury directly", reason="more specific routing",
            reviewer_name="ana2", created_at="2026-08-02T00:00:00+00:00",
        )
        block2 = corrections.correction_block_for("missing_bank_reference", tmp_dir)
        check("MAX_CORRECTIONS_PER_TYPE=1: only the MOST RECENT correction appears",
              "more specific routing" in block2 and "matcher's type maps to POLICY-007" not in block2,
              block2)

        by_type = corrections.load_corrections(tmp_dir)
        check("load_corrections() still returns the FULL history (truncation happens "
              "only at correction_block_for()'s read time, never by discarding on write)",
              len(by_type["missing_bank_reference"]) == 2, str(by_type))
        print()

        print("Section 2: agent/client.py's resolve_exception() end to end")
        from agent import client as agent_client

        captured = {}

        class _FakeProvider:
            def resolve(self, system_prompt, user_message):
                captured["system_prompt"] = system_prompt
                from agent.schema import ExceptionResolution
                return ExceptionResolution(
                    exception_type="missing_bank_reference", policy_id="POLICY-004",
                    root_cause="test", evidence_used=["settlement_id"],
                    recommended_action="test", confidence=0.9, sufficient_evidence=True,
                )

        real_get_provider = agent_client.get_active_provider
        agent_client.get_active_provider = lambda: _FakeProvider()
        try:
            row = {
                "final_exception_type": "missing_bank_reference",
                "settlement_id": "setl_test", "match_status": "no_settlement",
                "transaction_id": "trn-x",
            }
            agent_client.resolve_exception(row, data_dir=tmp_dir)
            check("resolve_exception() threads the correction block into the real system_prompt",
                  "more specific routing" in captured["system_prompt"],
                  captured.get("system_prompt", "")[-400:])

            other_row = {**row, "final_exception_type": "partial_refund",
                         "net_delta_rupees": 1.0, "ledger_expected_net_rupees": 1.0,
                         "observed_net_rupees": 0.0}
            captured.clear()
            agent_client.resolve_exception(other_row, data_dir=tmp_dir)
            check("a case of a DIFFERENT exception_type gets no correction text at all",
                  "more specific routing" not in captured["system_prompt"]
                  and "PREVIOUSLY CORRECTED" not in captured["system_prompt"])
        finally:
            agent_client.get_active_provider = real_get_provider
        print()

        print("Section 3: investigator/loop.py's investigate() end to end")
        from investigator.loop import investigate
        from investigator.tools import ToolContext

        inv_captured = {}

        class _FakeOllamaClient:
            def chat_with_tools(self, messages, tools):
                inv_captured["system_prompt"] = messages[0]["content"]
                return {"tool_calls": None}  # ends the loop immediately, round 1

            def final_answer(self, messages):
                return {
                    "exception_type": "missing_bank_reference", "policy_id": "POLICY-004",
                    "root_cause": "test", "evidence_used": [], "recommended_action": "test",
                    "confidence": 0.9, "sufficient_evidence": True,
                }

        ctx = ToolContext.__new__(ToolContext)  # not exercised -- chat_with_tools ends before any tool call
        row = {"final_exception_type": "missing_bank_reference", "transaction_id": "trn-x"}
        result = investigate(row, policy_block="[test policy]", evidence_block="[test evidence]",
                              ctx=ctx, client=_FakeOllamaClient(), data_dir=tmp_dir)
        check("investigate() threads the correction block into the real system_prompt too",
              "more specific routing" in inv_captured["system_prompt"],
              inv_captured.get("system_prompt", "")[-400:])
        check("the loop still produces a normal, valid InvestigationResult "
              "(the correction block doesn't interfere with the rest of the flow)",
              result.exception_type == "missing_bank_reference" and result.stopped_reason == "model_finished",
              str(result))
        print()

        print("Section 4: the write side (review_backend/main.py's submit_review())")
        print("  covered by test_review_api.py's own isolated-DB test suite, not duplicated here --")
        print("  see its 'Correction memory' section.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
