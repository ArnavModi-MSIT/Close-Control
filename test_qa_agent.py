"""Tests for qa_agent/ -- the Settlement Q&A agent.

Same "prove it against the real, unmodified production function" pattern
as test_ambiguity.py/test_chargeback.py/test_loan_recovery.py: tool tests
run against the real curated dataset, not a synthetic fixture, since the
tools ARE thin wrappers over already-verified computations
(matching/root_cause.py, cash_position/engine.py) -- the real numbers are
the assertion. The one genuinely new, safety-critical piece (grounding.py)
gets its own adversarial proof: a real hallucinated number must actually
be caught, not just assumed to be.
"""

import sys

from run_matcher import run as run_matcher
from matching.loaders import load_sources, load_loan_book
from qa_agent import grounding
from qa_agent.tools import ToolContext, get_portfolio_summary, search_cases, get_root_cause_summary, \
    get_cash_position_summary
from qa_agent.loop import ask
from qa_agent.schema import ToolCallRecord

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail}")


DATA_DIR = "data"
_report, _settlement_matches, _ = run_matcher(DATA_DIR)
_gateway, _bank, _ledger = load_sources(DATA_DIR)
_ctx = ToolContext(_report, _gateway, _bank, _settlement_matches, loan_book=load_loan_book(DATA_DIR))


def section(title):
    print(f"\n{title}")


# ---------------------------------------------------------------- grounding.py

section("Numeric extraction (extract_numbers)")
check("plain integer extracted", grounding.extract_numbers("There are 617 cases.") == [617.0])
check("comma-grouped rupee amount extracted, commas stripped",
      grounding.extract_numbers("Rs.5,54,612.74 at risk") == [554612.74])
check("percentage extracted as its raw number",
      grounding.extract_numbers("automation rate is 70.2%") == [70.2])
check("does NOT match digits inside a transaction id",
      grounding.extract_numbers("case trn-000237 is escalated") == [],
      str(grounding.extract_numbers("case trn-000237 is escalated")))
check("multiple numbers in one sentence, all found",
      grounding.extract_numbers("617 cases worth Rs.67,64,706.74, i.e. 29.8% of volume")
      == [617.0, 6764706.74, 29.8])
check("a rupee amount immediately followed by a SENTENCE-ENDING period is extracted in full, "
      "not truncated at the first comma (real bug found live: a genuine Ollama answer ending "
      "'...at risk is Rs.554,612.74.' was mis-extracting only 554)",
      grounding.extract_numbers("The amount at risk is Rs.554,612.74.") == [554612.74],
      str(grounding.extract_numbers("The amount at risk is Rs.554,612.74.")))

section("Grounded-number collection (collect_grounded_numbers)")
_sample_log = [
    ToolCallRecord(step=1, tool_name="get_portfolio_summary", arguments={},
                   result={"escalated_count": 617, "escalated_amount_at_risk_rupees": 6764706.74,
                            "escalated_by_exception_type": {"missing_bank_reference": 497}}),
]
_grounded = grounding.collect_grounded_numbers(_sample_log)
check("nested dict values collected", 617.0 in _grounded and 6764706.74 in _grounded and 497.0 in _grounded,
      str(_grounded))

section("check_grounding() -- the adversarial proof")
_good_answer = "There are 617 escalated cases worth Rs.67,64,706.74 in total."
_good_check = grounding.check_grounding(_good_answer, _sample_log)
check("a genuinely grounded answer passes with no ungrounded numbers",
      _good_check.all_grounded, str(_good_check.ungrounded_numbers))

_rounded_answer = "There are 617 escalated cases worth about Rs.67,64,707 in total."  # 1 rupee rounding
_rounded_check = grounding.check_grounding(_rounded_answer, _sample_log)
check("a trivially-rounded restatement of a real number still counts as grounded (tolerance)",
      _rounded_check.all_grounded, str(_rounded_check.ungrounded_numbers))

_hallucinated_answer = "There are 617 escalated cases worth Rs.99,99,999.99 in total."  # invented total
_bad_check = grounding.check_grounding(_hallucinated_answer, _sample_log)
check("a genuinely invented number IS caught, not waved through",
      not _bad_check.all_grounded and 9999999.99 in _bad_check.ungrounded_numbers,
      str(_bad_check.ungrounded_numbers))
check("the real, grounded number in the SAME hallucinated answer is not ALSO falsely flagged",
      617.0 not in _bad_check.ungrounded_numbers, str(_bad_check.ungrounded_numbers))

# Real bug found live, not in a unit test: a genuine Ollama answer to
# "show me the largest missing_bank_reference cases" said "...the total
# number of such cases is 497, and 20 of them are returned in this query.
# The remaining 477 cases are truncated." -- 477 = 497 - 20, a correct,
# simple derivation from two genuinely grounded numbers (both real fields
# in search_cases()'s actual tool result), never invented, but flagged
# "ungrounded" anyway since 477 itself never appears as a literal value in
# any tool result. Same "must not cry wolf on real evidence" failure mode
# already fixed once for evidence citations (agent/evidence.py).
_derived_log = [
    ToolCallRecord(step=1, tool_name="search_cases",
                   arguments={"exception_type": "missing_bank_reference", "limit": 20},
                   result={"total_matches": 497, "returned_count": 20, "truncated": True}),
]
_derived_answer = ("The total number of such cases is 497, and 20 of them are returned in this "
                    "query. The remaining 477 cases are truncated.")
_derived_check = grounding.check_grounding(_derived_answer, _derived_log)
check("a number that's a simple sum/difference of two REAL grounded numbers "
      "(477 = 497 - 20) is correctly treated as grounded, not flagged",
      _derived_check.all_grounded, str(_derived_check.ungrounded_numbers))

_still_invented_answer = "The total number of such cases is 497, and 20 are returned; 12345 are truncated."
_still_invented_check = grounding.check_grounding(_still_invented_answer, _derived_log)
check("but a genuinely invented number that ISN'T a real sum/difference of grounded "
      "values is still correctly caught -- the derived-number allowance doesn't open a "
      "real loophole",
      not _still_invented_check.all_grounded and 12345.0 in _still_invented_check.ungrounded_numbers,
      str(_still_invented_check.ungrounded_numbers))


# ---------------------------------------------------------------- tools.py, real data

section("get_portfolio_summary() against the real dataset")
_summary = get_portfolio_summary(_ctx)
check("clean + matcher-auto-resolved + escalated == total (conservation)",
      _summary["clean_count"] + _summary["matcher_auto_resolved_count"] + _summary["escalated_count"]
      == _summary["total_transactions"], str(_summary))
check("escalated_amount_at_risk_rupees is a positive real figure",
      _summary["escalated_amount_at_risk_rupees"] > 0)
check("escalated_by_exception_type is non-empty and sums to escalated_count",
      sum(_summary["escalated_by_exception_type"].values()) == _summary["escalated_count"],
      str(_summary["escalated_by_exception_type"]))

section("search_cases() against the real dataset")
_all_escalated = search_cases(_ctx, limit=10000)
check("total_matches with no filter equals get_portfolio_summary's escalated_count",
      _all_escalated["total_matches"] == _summary["escalated_count"])
_missing_ref = search_cases(_ctx, exception_type="missing_bank_reference", limit=5)
check("filtering by exception_type returns only that type",
      all(c["exception_type"] == "missing_bank_reference" for c in _missing_ref["cases"]),
      str(_missing_ref["cases"]))
check("a tight limit correctly reports truncated=True with the true total preserved",
      _missing_ref["truncated"] and _missing_ref["total_matches"] > _missing_ref["returned_count"],
      str(_missing_ref))
check("results are sorted by amount at risk descending",
      all(_missing_ref["cases"][i]["amount_at_risk_rupees"] >= _missing_ref["cases"][i + 1]["amount_at_risk_rupees"]
          for i in range(len(_missing_ref["cases"]) - 1)))

section("get_root_cause_summary() against the real dataset")
_rc = get_root_cause_summary(_ctx)
check("root_cause_clusters is a positive number well below escalated_cases (real compression)",
      0 < _rc["root_cause_clusters"] < _rc["escalated_cases"], str(_rc))
check("top_clusters is non-empty and sorted by case_count descending",
      len(_rc["top_clusters"]) > 0 and all(
          _rc["top_clusters"][i]["case_count"] >= _rc["top_clusters"][i + 1]["case_count"]
          for i in range(len(_rc["top_clusters"]) - 1)))

section("get_cash_position_summary() against the real dataset")
_cp = get_cash_position_summary(_ctx)
check("confirmed + in_transit rupees sum to the projected figure (by definition)",
      abs((_cp["confirmed_rupees"] + _cp["in_transit_rupees"]) - _cp["projected_cash_position_rupees"]) < 0.02,
      str(_cp))


# ---------------------------------------------------------------- loop.py, fake client

section("ask() end to end, with a fake client (no live Ollama needed)")


class FakeClientGrounded:
    """Calls get_portfolio_summary once, then answers using only the real
    number that call returned."""
    def __init__(self):
        self.model = "fake-model"
        self._round = 0

    def chat_with_tools(self, messages, tool_schemas):
        self._round += 1
        if self._round == 1:
            return {"role": "assistant",
                    "tool_calls": [{"function": {"name": "get_portfolio_summary", "arguments": "{}"}}]}
        return {"role": "assistant", "tool_calls": None}

    def final_answer(self, messages, schema_instruction=None):
        # Read the real number back out of the tool result the loop just
        # appended, so this fake client's answer is genuinely grounded --
        # not hardcoded, so a real change to the dataset can't silently
        # make this test meaningless.
        import json as _json
        tool_msg = next(m for m in reversed(messages) if m["role"] == "tool")
        real = _json.loads(tool_msg["content"])
        return {
            "answer": f"There are {real['escalated_count']} escalated cases.",
            "citations": ["TOOL-1"],
        }


_result = ask("How many cases are escalated?", _ctx, FakeClientGrounded())
check("QAResult has the real tool call recorded", len(_result.tool_log) == 1
      and _result.tool_log[0].tool_name == "get_portfolio_summary")
check("citations pass through from the model's final answer", _result.citations == ["TOOL-1"])
check("a genuinely grounded fake answer produces all_grounded=True, no warning appended",
      _result.grounding.all_grounded and "GROUNDING WARNING" not in _result.answer, _result.answer)


class FakeClientHallucinating:
    """Calls get_portfolio_summary, then answers with an invented number
    that was never in any tool result -- proves the loop's grounding
    check actually fires end to end, not just in isolation."""
    def __init__(self):
        self.model = "fake-model"
        self._round = 0

    def chat_with_tools(self, messages, tool_schemas):
        self._round += 1
        if self._round == 1:
            return {"role": "assistant",
                    "tool_calls": [{"function": {"name": "get_portfolio_summary", "arguments": "{}"}}]}
        return {"role": "assistant", "tool_calls": None}

    def final_answer(self, messages, schema_instruction=None):
        return {"answer": "There are exactly 424242 escalated cases.", "citations": ["TOOL-1"]}


_bad_result = ask("How many cases are escalated?", _ctx, FakeClientHallucinating())
check("a hallucinated fake answer is flagged: all_grounded=False", not _bad_result.grounding.all_grounded)
check("the warning is actually appended to the answer text a human would read",
      "GROUNDING WARNING" in _bad_result.answer, _bad_result.answer)


class FakeClientToolFailure:
    """A tool call that raises must not crash ask() -- same widened
    except-Exception fix already applied to investigator/loop.py."""
    def __init__(self):
        self.model = "fake-model"
        self._round = 0

    def chat_with_tools(self, messages, tool_schemas):
        self._round += 1
        if self._round == 1:
            return {"role": "assistant",
                    "tool_calls": [{"function": {"name": "search_cases",
                                                   "arguments": '{"min_amount_rupees": "not-a-number"}'}}]}
        return {"role": "assistant", "tool_calls": None}

    def final_answer(self, messages, schema_instruction=None):
        return {"answer": "I could not complete that lookup.", "citations": []}


try:
    _err_result = ask("bad question", _ctx, FakeClientToolFailure())
    check("a tool call that raises (bad arg type) degrades to a recorded error, does not crash ask()",
          "error" in _err_result.tool_log[0].result, str(_err_result.tool_log[0].result))
except Exception as e:
    check("a tool call that raises (bad arg type) degrades to a recorded error, does not crash ask()",
          False, f"ask() itself raised: {type(e).__name__}: {e}")


print(f"\n{'=' * 70}")
print(f"{passed} passed, {failed} failed")
print(f"{'=' * 70}")
if failed:
    sys.exit(1)
