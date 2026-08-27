"""
Tool-using investigation agent -- CLI entrypoint.

Runs a small sample of escalated cases through the multi-step investigation
loop (investigator/) instead of the single-shot agent (agent/client.py),
prints the full tool-call trace for each, then runs the SAME agent/gate.py
against the result to prove it's a drop-in-compatible upgrade, not a
parallel system with its own rules.

Requires Ollama running locally with qwen3:8b pulled (chosen over the
llama3.1:8b the rest of agent/ uses specifically for tool-calling
reliability -- see investigator/config.py).

    python run_investigator.py
    python run_investigator.py --n 5
    python run_investigator.py --n 5 --concurrency 3   # investigate 3 cases at once instead
                                                        # of one at a time -- see --help
    python run_investigator.py --transaction-id trn-000001
"""

import os
import sys
import json
import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

# The model's own generated text (root_cause, drafted_communication, etc.)
# can contain a rupee sign (U+20B9) or other non-ASCII characters we don't
# control -- Windows consoles default to cp1252, which can't encode it and
# crashes print() outright. Same fix as test_ambiguity.py, needed here for
# a different reason: there the ₹ was a string literal we wrote, here it's
# whatever the LLM decides to output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from run_matcher import run
from matching.loaders import load_sources
from agent.policy_kb import get_policy
from agent.evidence import build_evidence, build_policy_block
from agent.gate import apply_gate, is_investigation_worthwhile
from investigator import config
from investigator.tools import ToolContext
from investigator.loop import investigate, tool_evidence_ids
from investigator.ollama_client import OllamaToolClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_PATH = os.path.join(DATA_DIR, "investigation_log.jsonl")


def stratified_sample(escalated: pd.DataFrame, n_per_type: int) -> pd.DataFrame:
    parts = [group.head(n_per_type) for _, group in escalated.groupby("final_exception_type")]
    return pd.concat(parts, ignore_index=True) if parts else escalated.iloc[0:0]


def print_case_trace(row_dict: dict, result, gate_result: dict):
    print("=" * 70)
    print(f"CASE {row_dict['transaction_id']}  ({row_dict['final_exception_type']})")
    print("=" * 70)
    print(f"Amount at risk: Rs.{gate_result['amount_at_risk_rupees']:,.2f}")
    print()
    print(f"Tool rounds used: {result.tool_rounds_used}/{result.tool_rounds_used if result.stopped_reason=='max_rounds_exceeded' else result.tool_rounds_used} "
          f"({result.stopped_reason})  |  elapsed: {result.elapsed_seconds:.1f}s")
    for record in result.investigation_log:
        print(f"  [{record.step}] {record.tool_name}({', '.join(f'{k}={v!r}' for k, v in record.arguments.items())})")
        result_preview = json.dumps(record.result, default=str)
        print(f"      -> {result_preview[:160]}{'...' if len(result_preview) > 160 else ''}")
    print()
    print(f"Verdict: {result.exception_type}  |  policy {result.policy_id}  |  confidence {result.confidence:.2f}")
    print(f"Root cause: {result.root_cause}")
    print(f"Investigation summary: {result.investigation_summary}")
    print(f"Recommended: {result.recommended_action}")
    if result.drafted_communication:
        print(f"Drafted communication:\n  {result.drafted_communication}")
    print()
    print(f"GATE DECISION: {gate_result['final_decision']}  ({gate_result['agent_status']})")
    for reason in gate_result["gate_reasons"]:
        print(f"  - {reason}")
    print()


def _investigate_one(row_dict: dict, ctx: ToolContext, client: OllamaToolClient):
    """Runs on a worker thread when --concurrency > 1. ctx and client are
    both safe to share across threads: ToolContext's DataFrames/dicts are
    built once and only ever read (never mutated) by tool calls, and
    OllamaToolClient holds no mutable per-call state -- every call builds
    its own payload and makes an independent request. apply_gate,
    print_case_trace, and log_entries all stay in the main thread (see
    main()) so there's no shared-state race to guard against here."""
    exc_type = row_dict["final_exception_type"]
    try:
        policy = get_policy(exc_type)
        policy_block = build_policy_block(policy, policy["policy_id"])
    except KeyError:
        policy_block = "[NO MATCHING POLICY FOUND -- treat as insufficient evidence.]"
    evidence_block = build_evidence(row_dict)
    result = investigate(row_dict, policy_block, evidence_block, ctx, client)
    return row_dict, result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=1, help="Cases per exception type (default 1, keep small -- each case is several LLM calls). "
                                                          "With --exception-type, this is a total count instead of a per-type count.")
    parser.add_argument("--transaction-id", default=None, help="Investigate one specific transaction_id instead of sampling")
    parser.add_argument("--exception-type", default=None,
                         help="Restrict sampling to one final_exception_type instead of stratifying across all "
                              "types -- useful for topping up coverage on whichever type has the biggest backlog "
                              "without re-touching types that are already fully investigated. Cases already "
                              "present in the investigation log are skipped so a Kaggle/remote run doesn't waste "
                              "GPU time repeating work already done locally.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--model", default=None,
                         help=f"Ollama model to use instead of the default ({config.INVESTIGATOR_MODEL}). "
                              f"'qwen3:8b' is the fallback if the default's small validation sample (4 "
                              f"cases across 4 exception types, 0 tool-call errors) ever turns up a "
                              f"reliability issue on a broader batch -- it's the one with the larger "
                              f"published tool-calling benchmark behind it (0.933 F1), just ~160s/case "
                              f"instead of ~48-97s/case. Any model you've pulled with `ollama pull` works.")
    parser.add_argument("--reachable-only", action="store_true",
                         help="Restrict sampling and --exception-type targeting to cases where "
                              "the matcher's own exception_type + amount at risk make auto-resolve "
                              "structurally reachable at all (see agent.gate.is_investigation_worthwhile) "
                              "-- i.e. skip cases that will escalate regardless of investigation depth, "
                              "so GPU/Ollama time only goes where the extra reasoning could change the "
                              "outcome. Does NOT affect --transaction-id: an explicit request is always "
                              "honored, never silently filtered.")
    parser.add_argument("--concurrency", type=int, default=1,
                         help="Investigate this many cases at once instead of one at a time "
                              "(default 1, today's sequential behavior, unchanged). Threads, "
                              "not processes -- each investigation is blocking network calls to "
                              "Ollama, not CPU-bound work. A single case's own tool-call rounds "
                              "stay strictly sequential either way (each round depends on the "
                              "previous one's result) -- this only parallelizes ACROSS cases. "
                              "Ollama is one process sharing your CPU's core pool across every "
                              "concurrent request, so expect a real but sub-linear speedup, not "
                              "N times faster -- also depends on Ollama's own OLLAMA_NUM_PARALLEL.")
    args = parser.parse_args()
    if args.concurrency < 1:
        print("ERROR: --concurrency must be >= 1")
        raise SystemExit(1)

    report, settlement_matches, ledger_check = run(args.data_dir)
    gateway, bank, ledger = load_sources(args.data_dir)
    ctx = ToolContext(report, gateway, bank, settlement_matches)
    client = OllamaToolClient(model=args.model or config.INVESTIGATOR_MODEL)

    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]

    reachable_mask = escalated.apply(lambda r: is_investigation_worthwhile(r.to_dict()), axis=1)
    reachable_count = int(reachable_mask.sum())
    print(f"Escalated backlog: {len(escalated)} total, {reachable_count} structurally reachable for "
          f"auto-resolve (matcher exception_type in the allowlist + amount under the risk ceiling) -- "
          f"only those can have investigation depth change the gate's decision; the rest will "
          f"escalate regardless of how thoroughly they're investigated.")

    sampling_pool = escalated[reachable_mask] if args.reachable_only else escalated
    if args.reachable_only:
        print(f"--reachable-only: sampling restricted to {len(sampling_pool)} reachable case(s).")

    if args.transaction_id:
        # Always checked against the FULL escalated pool, never sampling_pool --
        # an explicit request is honored even if --reachable-only was also passed.
        cases = escalated[escalated["transaction_id"] == args.transaction_id]
        if cases.empty:
            print(f"'{args.transaction_id}' is not an escalated case.")
            raise SystemExit(1)
    elif args.exception_type:
        already = set()
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        already.add(json.loads(line)["transaction_id"])
        of_type = sampling_pool[sampling_pool["final_exception_type"] == args.exception_type]
        cases = of_type[~of_type["transaction_id"].isin(already)].head(args.n)
        if cases.empty:
            print(f"No un-investigated '{args.exception_type}' cases left "
                  f"({len(of_type)} of that type exist{' in the reachable pool' if args.reachable_only else ''}, "
                  f"all already in {LOG_PATH}).")
            raise SystemExit(1)
    else:
        cases = stratified_sample(sampling_pool, args.n)

    print(f"Provider: ollama ({client.model})")
    print(f"Cases: {len(cases)}")
    if args.concurrency > 1:
        print(f"Concurrency: {args.concurrency} (see --help for what this does and doesn't buy you)")
    print()

    written = 0

    def _handle_result(row_dict, result, log_file):
        nonlocal written
        gate_result = apply_gate(result, row_dict, tool_evidence_ids(result))
        print_case_trace(row_dict, result, gate_result)
        entry = {"transaction_id": row_dict["transaction_id"], **result.model_dump(),
                 "gate_decision": gate_result["final_decision"],
                 "gate_agent_status": gate_result["agent_status"],
                 "model": client.model,
                 "investigated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        log_file.write(json.dumps(entry, default=str) + "\n")
        log_file.flush()
        written += 1

    # Opened once, appended to after EVERY case (not batched at the end) --
    # a multi-hour run over dozens of cases needs to survive being cancelled
    # partway without losing everything already completed.
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        if args.concurrency == 1:
            for _, row in cases.iterrows():
                row_dict, result = _investigate_one(row.to_dict(), ctx, client)
                _handle_result(row_dict, result, log_file)
        else:
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                futures = [executor.submit(_investigate_one, row.to_dict(), ctx, client)
                           for _, row in cases.iterrows()]
                for future in as_completed(futures):
                    row_dict, result = future.result()
                    _handle_result(row_dict, result, log_file)

    print(f"Wrote {written} investigation record(s) to {LOG_PATH}")


if __name__ == "__main__":
    main()
