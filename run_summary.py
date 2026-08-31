"""
Generates a whole-run natural-language summary -- CLI entrypoint.

Runs the deterministic matcher + root-cause clustering fresh, has the
summary narrated (mock by default, $0; --provider ollama for a real local
model), and writes data/run_summary.txt. review_backend/main.py's
GET /api/run-summary serves whatever's on disk -- this script is what
regenerates it, exactly the same "pre-computed, then served statically"
pattern export_dashboard_data.py already uses for dashboard_data.json, so
the API never triggers an LLM call on request.

    python run_summary.py
    python run_summary.py --provider ollama
"""

import os
import argparse

from run_matcher import run
from matching.root_cause import cluster_escalated_cases, summarize
from agent.run_summary import build_run_summary

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--provider", default="mock", choices=["mock", "ollama"])
    parser.add_argument("--model", default="llama3.1:8b")
    parser.add_argument("--out", default=os.path.join(DATA_DIR, "run_summary.txt"))
    args = parser.parse_args()

    report, _, _ = run(args.data_dir)
    escalated_count = int((report["final_exception_type"].notna() & (~report["auto_resolve_eligible"])).sum())
    clusters = cluster_escalated_cases(report)
    root_cause_summary = summarize(clusters, escalated_count)

    summary_text = build_run_summary(report, root_cause_summary, provider=args.provider, model=args.model)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(summary_text)

    print(summary_text)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
