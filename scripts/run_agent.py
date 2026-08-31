"""
Exception Resolution Agent -- CLI entrypoint.

Three execution modes:

  --mode mock    (default) All escalated cases through the $0 mock provider.
                  No network call, ever. Safe to run repeatedly.
  --mode sample   ~2 cases per exception type (~18-20 calls) through the
                  provider configured via LLM_PROVIDER in .env.
  --mode full     ALL escalated cases through the configured provider.

    python scripts/run_agent.py --mode mock
    python scripts/run_agent.py --mode sample
    python scripts/run_agent.py --mode full
    python scripts/run_agent.py --mode full --concurrency 4   # dispatch 4 cases at once instead
                                                       # of one at a time -- see --help for
                                                       # what this does and doesn't buy you

Audit entries (data/audit_log.jsonl) accumulate across runs by default -- each
entry carries its own agent_run_id, so runs stay filterable/groupable in the
same file. Pass --reset-log to start a clean file instead.
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
import json
import time
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from run_matcher import run
from agent import config
from agent.client import resolve_exception, get_active_provider
from agent.gate import apply_gate
from agent.audit import write_entry, reset_log, RUN_ID

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SAMPLE_SIZE_PER_TYPE = 2

REASON_CATEGORIES = [
    ("is not in AGENT_AUTO_RESOLVABLE_TYPES", "not_in_automation_allowlist"),
    ("no policy found", "policy_missing"),
    ("agent reclassified", "agent_reclassified_type"),
    ("citation mismatch", "policy_id_citation_mismatch"),
    ("confidence", "confidence_below_threshold"),
    ("evidence as insufficient", "agent_flagged_insufficient_evidence"),
    ("exceeds risk ceiling", "amount_exceeds_risk_ceiling"),
    ("all gate conditions satisfied", "all_conditions_met"),
]


def categorize_reason(reason: str) -> str:
    for substring, category in REASON_CATEGORIES:
        if substring in reason:
            return category
    return "other"


def stratified_sample(escalated, n_per_type):
    parts = []
    for _, group in escalated.groupby("final_exception_type"):
        parts.append(group.head(n_per_type))
    return pd.concat(parts, ignore_index=True) if parts else escalated.iloc[0:0]


def confirm_full_run_with_paid_provider():
    print(f"WARNING: --mode full with LLM_PROVIDER={config.LLM_PROVIDER} will make a real")
    print("API call for every escalated case. If this provider is paid (anthropic), this")
    print("will cost real money.")
    answer = input("Type 'yes' to continue, anything else to abort: ").strip().lower()
    if answer != "yes":
        print("Aborted.")
        raise SystemExit(0)


def _resolve_one(row_dict: dict):
    """Runs on a worker thread when --concurrency > 1. Deliberately does ONLY
    the network-bound work (resolve_exception) -- no shared-state mutation
    happens here. apply_gate/write_entry/results-tracking all stay in the
    main thread (see main()), so there's nothing to lock: the only thing
    running concurrently is independent HTTP calls to the provider."""
    t0 = time.perf_counter()
    resolution = resolve_exception(row_dict)
    elapsed = time.perf_counter() - t0
    return row_dict, resolution, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "sample", "full"], default="mock")
    parser.add_argument("--reset-log", action="store_true",
                         help="Wipe data/audit_log.jsonl before this run. Default is to append, "
                              "since an audit trail that gets silently erased on every run isn't "
                              "an audit trail.")
    parser.add_argument("--only-new", action="store_true",
                         help="Skip any transaction_id that already has an entry in the audit "
                              "log, resolving only cases it has never seen. Use this after the "
                              "dataset gains transactions (e.g. data_generation/chargebacks.py's "
                              "appended chargeback space) so the existing frozen proposals keep "
                              "their original run_id/timestamp -- and therefore their "
                              "seed_review_queue.py hashes -- instead of being re-proposed under "
                              "a new run and flagged as 603 conflicts. Same 'top up, don't "
                              "redo' idea as run_investigator.py's --exception-type dedup and "
                              "run_stream_simulator.py's already_processed_transaction_ids().")
    parser.add_argument("--concurrency", type=int, default=1,
                         help="Dispatch this many resolve_exception() calls at once instead of "
                              "one at a time (default 1, i.e. today's sequential behavior, "
                              "unchanged). Threads, not processes -- these are blocking network "
                              "calls, not CPU-bound work, so this doesn't need multiprocessing. "
                              "Real speedup depends on the provider: Groq/Anthropic handle "
                              "concurrent requests natively. Local Ollama is one process sharing "
                              "your CPU's core pool across every concurrent request AND Ollama's "
                              "own OLLAMA_NUM_PARALLEL setting -- expect a real but sub-linear "
                              "improvement, not N times faster.")
    args = parser.parse_args()
    if args.concurrency < 1:
        print("ERROR: --concurrency must be >= 1")
        raise SystemExit(1)

    if args.mode == "mock":
        config.LLM_PROVIDER = "mock"
        config.OFFLINE_MODE = True

    print(f"Run ID: {RUN_ID}")
    print(f"Mode: {args.mode}")
    print(f"Provider: {config.LLM_PROVIDER}"
          + (f" ({config.GROQ_MODEL})" if config.LLM_PROVIDER == "groq" else "")
          + (f" ({config.OLLAMA_MODEL})" if config.LLM_PROVIDER == "ollama" else ""))
    print()

    report, settlement_matches, ledger_check = run(DATA_DIR)

    escalated = report[report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])]
    print(f"Total exceptions from matcher: {report['final_exception_type'].notna().sum()}")
    print(f"Already deterministically auto-resolved (no agent needed): "
          f"{(report['final_exception_type'].notna() & report['auto_resolve_eligible']).sum()}")
    print(f"Escalated (candidates for the agent): {len(escalated)}")

    if args.only_new:
        already = set()
        if os.path.exists(config.AUDIT_LOG_PATH):
            with open(config.AUDIT_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        already.add(json.loads(line)["transaction_id"])
        before = len(escalated)
        escalated = escalated[~escalated["transaction_id"].isin(already)]
        print(f"--only-new: {before - len(escalated)} already in the audit log, "
              f"{len(escalated)} genuinely new case(s) to resolve.")
        if escalated.empty:
            print("Nothing new to do -- every escalated case already has a proposal on record.")
            return

    if args.mode == "sample":
        escalated = stratified_sample(escalated, SAMPLE_SIZE_PER_TYPE)
        print(f"Sample mode: running {len(escalated)} cases "
              f"(~{SAMPLE_SIZE_PER_TYPE} per exception type)")
    elif args.mode == "full" and config.LLM_PROVIDER != "mock":
        confirm_full_run_with_paid_provider()

    print()

    if config.LLM_PROVIDER != "mock":
        try:
            provider = get_active_provider()
        except RuntimeError as e:
            print(f"ERROR: {e}")
            raise SystemExit(1)
    else:
        provider = get_active_provider()

    if args.reset_log:
        reset_log()

    if args.concurrency > 1:
        print(f"Concurrency: {args.concurrency} (see --help for what this does and doesn't buy you)")
        if config.LLM_PROVIDER == "ollama":
            print("Provider is ollama: real speedup also depends on Ollama's own "
                  "OLLAMA_NUM_PARALLEL setting, not just this flag.")
        print()

    def _handle_result(row_dict, resolution, elapsed, results, reason_counter):
        gate_result = apply_gate(resolution, row_dict)
        write_entry(row_dict, resolution, gate_result, provider,
                    elapsed_seconds=None if config.LLM_PROVIDER == "mock" else round(elapsed, 3))
        for reason in gate_result["gate_reasons"]:
            reason_counter[categorize_reason(reason)] += 1
        results.append({
            "transaction_id": row_dict["transaction_id"],
            "exception_type": row_dict["final_exception_type"],
            "agent_confidence": resolution.confidence,
            "sufficient_evidence": resolution.sufficient_evidence,
            "gate_decision": gate_result["final_decision"],
            "agent_status": gate_result["agent_status"],
            "reclassified": gate_result["reclassified"],
            "elapsed_seconds": None if config.LLM_PROVIDER == "mock" else elapsed,
        })

    results = []
    reason_counter = Counter()
    total = len(escalated)

    if args.concurrency == 1:
        for i, (_, row) in enumerate(escalated.iterrows()):
            row_dict, resolution, elapsed = _resolve_one(row.to_dict())
            _handle_result(row_dict, resolution, elapsed, results, reason_counter)
            if (i + 1) % 50 == 0 or (i + 1) == total:
                print(f"  processed {i + 1}/{total}")
    else:
        # Threads, not processes: resolve_exception() is I/O-bound (a blocking
        # HTTP call), so the GIL is released for the duration of each request --
        # this genuinely runs requests concurrently, no multiprocessing needed.
        # apply_gate/write_entry/results stay entirely in the main thread as each
        # future completes, so there's no shared-state race to guard against --
        # the only thing actually running on worker threads is resolve_exception.
        completed = 0
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(_resolve_one, row.to_dict()) for _, row in escalated.iterrows()]
            for future in as_completed(futures):
                row_dict, resolution, elapsed = future.result()
                _handle_result(row_dict, resolution, elapsed, results, reason_counter)
                completed += 1
                if completed % 50 == 0 or completed == total:
                    print(f"  processed {completed}/{total}")

    results_df = pd.DataFrame(results)

    print()
    print("=" * 70)
    print("AGENT RUN SUMMARY")
    print("=" * 70)
    print(f"Mode: {args.mode} | Provider: {provider.name} ({provider.model})")
    print(f"Total processed: {len(results_df)}")
    print()
    print("Gate decisions:")
    print(results_df["gate_decision"].value_counts())
    print()
    print("Agent status breakdown (why each entry looks the way it does):")
    print(results_df["agent_status"].value_counts())
    print()
    print(f"Cases where the agent reclassified the exception type "
          f"(informational only -- did not change gate authority): "
          f"{results_df['reclassified'].sum()}")
    print()
    print("By exception type -> gate decision:")
    print(results_df.groupby(["exception_type", "gate_decision"]).size().unstack(fill_value=0))
    print()
    print("Gate reason categories (why cases did/didn't auto-resolve, aggregated):")
    for reason, count in reason_counter.most_common():
        print(f"  {count:4d}  {reason}")
    print()
    print(f"Mean agent confidence: {results_df['agent_confidence'].mean():.2f}")
    print(f"Sufficient evidence rate: {results_df['sufficient_evidence'].mean():.1%}")
    print()
    if results_df["elapsed_seconds"].notna().any():
        lat = results_df["elapsed_seconds"].dropna()
        print(f"LLM call latency ({len(lat)} live calls, mock provider not timed):")
        print(f"  mean: {lat.mean():.2f}s   p95: {lat.quantile(0.95):.2f}s   "
              f"total: {lat.sum():.1f}s")
        print()
    print(f"Full audit trail written to: {config.AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
