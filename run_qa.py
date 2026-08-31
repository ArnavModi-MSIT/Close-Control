"""
Settlement Q&A agent -- CLI entrypoint.

Direction #2 from the buildathon brief ("Settlement Q&A agent"), built as
an additive module (qa_agent/) alongside the existing multi-source
reconciliation loop -- same Ollama-first, tool-calling architecture as
investigator/, answering a free-text question instead of investigating
one fixed case. Every number in the answer is checked against what the
tools actually returned (qa_agent/grounding.py) before being shown.

Requires Ollama running locally with qwen3:1.7b pulled (same default
model as investigator/, same measured tool-calling reliability reasoning
-- see qa_agent/config.py).

    python run_qa.py "How much cash is confirmed right now?"
    python run_qa.py "What's driving the review queue backlog?"
    python run_qa.py "Show me the biggest missing_bank_reference cases" --model qwen3:8b
"""

import os
import sys
import argparse

# The model's own generated answer text can contain a rupee sign (U+20B9)
# -- Windows consoles default to cp1252, which can't encode it and crashes
# print() outright. Same fix as run_investigator.py, for the same reason.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from run_matcher import run as run_matcher
from matching.loaders import load_sources, load_loan_book
from qa_agent import config
from qa_agent.tools import ToolContext
from qa_agent.loop import ask
from investigator.ollama_client import OllamaToolClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def print_result(result):
    print(f"\nQ: {result.question}")
    print(f"A: {result.answer}")
    print(f"\nCitations: {', '.join(result.citations) if result.citations else '(none)'}")
    print(f"Tool rounds used: {result.tool_rounds_used} | stopped: {result.stopped_reason} | "
          f"elapsed: {result.elapsed_seconds}s | model: {result.model}")
    if result.tool_log:
        print("\nTool trace:")
        for entry in result.tool_log:
            print(f"  [step {entry.step}] {entry.tool_name}({entry.arguments}) -> "
                  f"{str(entry.result)[:200]}{'...' if len(str(entry.result)) > 200 else ''}")
    if not result.grounding.all_grounded:
        print(f"\nWARNING: ungrounded numbers in answer: {result.grounding.ungrounded_numbers}")
    else:
        print(f"\nAll {len(result.grounding.claimed_numbers)} number(s) in the answer are grounded "
              f"in real tool results.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", default=None,
                         help="The question to ask. If omitted, runs a small set of example questions.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--model", default=None,
                         help=f"Ollama model to use instead of the default ({config.QA_MODEL}).")
    args = parser.parse_args()

    report, settlement_matches, ledger_check = run_matcher(args.data_dir)
    gateway, bank, ledger = load_sources(args.data_dir)
    ctx = ToolContext(report, gateway, bank, settlement_matches,
                       loan_book=load_loan_book(args.data_dir))
    client = OllamaToolClient(model=args.model or config.QA_MODEL)

    print(f"Provider: ollama ({client.model})")

    questions = [args.question] if args.question else [
        "How many transactions are escalated right now, and what's the total amount at risk?",
        "What's actually driving the review queue backlog -- what's the single biggest root cause?",
        "What's the current cash position -- how much is confirmed vs in transit vs at risk?",
    ]

    for q in questions:
        result = ask(q, ctx, client)
        print_result(result)
        print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
