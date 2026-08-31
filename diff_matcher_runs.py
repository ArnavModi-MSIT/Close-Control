"""Diffs the deterministic matcher's OUTPUT between two versions -- either
two code versions on the same dataset, or the same code on two datasets.

WHY THIS EXISTS
evaluate.py answers "is the matcher still accurate against ground truth."
It does NOT answer "did this code change silently move any transaction from
one classification to another" -- a change can hold 100% accuracy while
still reclassifying real cases in ways ground truth doesn't penalize (this
project's own loan-recovery and chargeback additions are exactly that
shape: existing rows were untouched, but a shortfall that used to fall
through to unexplained_shortage or partial_refund now has a real, named
explanation). Idea adapted from DataRecce/recce's dbt-PR-review workflow --
profile/value diffs between a dev and prod environment before merging --
translated from "did my dbt model change the data" to "did my matcher
change land differently on real transactions."

Purely observational, same contract as matching/diagnostics.py and
matching/root_cause.py -- never imported by the matching path itself, never
changes a classification. A maintainer tool, not a demo artifact.

USAGE
    # Default: HEAD's matching/ code vs the CURRENT working tree (including
    # uncommitted edits), both run against the same real data/ directory.
    # This is the everyday "did my uncommitted change break something" check.
    python diff_matcher_runs.py

    # Two explicit code versions (git refs), same dataset:
    python diff_matcher_runs.py --before-ref HEAD~3 --after-ref HEAD

    # Same code, two datasets (e.g. main demo vs a seed-robustness regen):
    python diff_matcher_runs.py --before-dir data --after-dir data_seed_1337

--before-ref/--after-ref run the OTHER version's matcher in an isolated git
worktree + subprocess (never touches this process's already-imported
modules, never touches the caller's working tree) -- only when that side is
an explicit ref, not the default "working tree" side, which runs in-process
directly against whatever is on disk right now, uncommitted edits included.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# The columns whose flips this tool actually cares about -- what a matcher
# code change can move, per matching/report.py's build_report().
TRACKED_COLUMNS = ["final_exception_type", "auto_resolve_eligible", "risk_class"]

# Amount proxy for the profile-diff section, matching matching/root_cause.py's
# own convention (_amount_at_risk): the ledger's own expected net is what the
# business believed it was owed, which is the meaningful "how much money is
# in question" figure for an exception row -- not the possibly-untrustworthy
# observed side.
AMOUNT_COLUMN = "ledger_expected_net_rupees"


def _run_report_in_process(data_dir: str) -> pd.DataFrame:
    """Runs the matcher using whatever code is CURRENTLY on disk in this
    repo -- including uncommitted edits, since Python reads .py source
    fresh, not from any compiled/cached snapshot. This is deliberately the
    only path that can see uncommitted work; a ref-based run never can."""
    from run_matcher import run
    report, _, _ = run(data_dir)
    return report


def _run_report_at_ref(ref: str, data_dir: str) -> pd.DataFrame:
    """Runs the matcher using the code exactly as it existed at `ref`, via a
    temporary, detached git worktree -- never touches the caller's working
    tree, never touches this process's already-imported `matching`/
    `run_matcher` modules (a second `import run_matcher` in the same
    process would just return the FIRST version's cached module, which is
    exactly the bug an isolated subprocess avoids). `data_dir` is passed as
    an absolute path so BOTH sides read the exact same real dataset --
    only the code differs, which is the entire point of this mode."""
    data_dir_abs = os.path.abspath(data_dir)
    tmp_root = tempfile.mkdtemp(prefix="diff_matcher_worktree_")
    worktree_dir = os.path.join(tmp_root, "wt")
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", worktree_dir, ref],
            capture_output=True, text=True,
        )
        if add.returncode != 0:
            raise RuntimeError(f"could not check out ref {ref!r} into a worktree:\n{add.stderr}")

        out_csv = os.path.join(tmp_root, "report.csv")
        # Written into the worktree itself (not tmp_root) so `import
        # run_matcher` resolves to THAT worktree's copy via cwd -- no
        # sys.path surgery, no ambiguity about which version is running.
        worker_path = os.path.join(worktree_dir, "_diff_matcher_worker.py")
        with open(worker_path, "w", encoding="utf-8") as f:
            f.write(
                "from run_matcher import run\n"
                f"report, _, _ = run({data_dir_abs!r})\n"
                f"report.to_csv({out_csv!r}, index=False)\n"
            )

        # sys.executable -- the SAME interpreter (and venv) this script is
        # already running under, so the old code gets the right dependencies
        # without needing its own environment; only the source files differ.
        result = subprocess.run(
            [sys.executable, worker_path], cwd=worktree_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"matcher run at ref {ref!r} failed:\n{result.stderr}")
        return pd.read_csv(out_csv)
    finally:
        subprocess.run(["git", "worktree", "remove", worktree_dir, "--force"],
                        capture_output=True, text=True)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _get_report(*, ref: str | None, data_dir: str) -> pd.DataFrame:
    if ref is None:
        return _run_report_in_process(data_dir)
    return _run_report_at_ref(ref, data_dir)


def diff_reports(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """Pure function over two already-computed report DataFrames -- no I/O,
    no git, no subprocess. Takes the DataFrames rather than paths/refs so
    this is independently testable and reusable regardless of how the two
    sides were produced."""
    before = before.set_index("transaction_id")
    after = after.set_index("transaction_id")

    only_before = sorted(set(before.index) - set(after.index))
    only_after = sorted(set(after.index) - set(before.index))
    shared = sorted(set(before.index) & set(after.index))

    b = before.loc[shared]
    a = after.loc[shared]

    def _normalize_nullable(series: pd.Series) -> pd.Series:
        """A clean row's final_exception_type is Python None in an
        in-process DataFrame but becomes float NaN once round-tripped
        through the CSV a ref-based worktree run writes -- str(None) ==
        "None" while str(float("nan")) == "nan", so a naive astype(str)
        comparison flags every still-clean row as "changed" against itself
        (caught by running this tool for real: it reported 1397 false
        "nan -> None" transitions on a genuinely unchanged clean-row set,
        which would have been a deeply misleading result to publish).
        .notna() correctly treats both representations as null regardless
        of which one a given side happens to hold, so map both to one
        sentinel before any string comparison ever happens."""
        return series.where(series.notna(), "(no exception)").astype(str)

    transitions = {}
    changed_mask = pd.Series(False, index=shared)
    for col in TRACKED_COLUMNS:
        b_col = _normalize_nullable(b[col])
        a_col = _normalize_nullable(a[col])
        col_changed = b_col != a_col
        changed_mask = changed_mask | col_changed

        if col_changed.any():
            pairs = pd.DataFrame({"before": b_col[col_changed], "after": a_col[col_changed]})
            transitions[col] = (
                pairs.value_counts(["before", "after"])
                .reset_index(name="count")
                .sort_values("count", ascending=False)
                .to_dict(orient="records")
            )
        else:
            transitions[col] = []

    changed_ids = [t for t in shared if changed_mask[t]]

    def _amount_stats(df: pd.DataFrame) -> dict:
        exc = df[df["final_exception_type"].notna()]
        amt = exc[AMOUNT_COLUMN] if AMOUNT_COLUMN in exc.columns else pd.Series(dtype=float)
        return {"exception_count": int(len(exc)),
                "sum_rupees": round(float(amt.sum()), 2) if len(amt) else 0.0,
                "mean_rupees": round(float(amt.mean()), 2) if len(amt) else 0.0}

    return {
        "before_total": int(len(before)),
        "after_total": int(len(after)),
        "shared_total": int(len(shared)),
        "only_in_before": only_before,
        "only_in_after": only_after,
        "changed_count": int(len(changed_ids)),
        "changed_transaction_ids": changed_ids,
        "transitions": transitions,
        "amount_before": _amount_stats(before),
        "amount_after": _amount_stats(after),
    }


def print_diff_report(diff: dict, before_label: str, after_label: str) -> None:
    print("=" * 70)
    print(f"MATCHER OUTPUT DIFF: {before_label}  vs  {after_label}")
    print("=" * 70)
    print(f"Transactions -- before: {diff['before_total']}, after: {diff['after_total']}, "
          f"shared: {diff['shared_total']}")

    if diff["only_in_before"]:
        print(f"\n{len(diff['only_in_before'])} transaction(s) only in BEFORE (removed/renamed):")
        print(f"  {diff['only_in_before'][:10]}"
              + (" ..." if len(diff["only_in_before"]) > 10 else ""))
    if diff["only_in_after"]:
        print(f"\n{len(diff['only_in_after'])} transaction(s) only in AFTER (new):")
        print(f"  {diff['only_in_after'][:10]}"
              + (" ..." if len(diff["only_in_after"]) > 10 else ""))

    print()
    if diff["changed_count"] == 0:
        print(f"NO CHANGE -- all {diff['shared_total']} shared transactions classified "
              f"identically on {list(TRACKED_COLUMNS)}.")
    else:
        pct = diff["changed_count"] / diff["shared_total"] * 100 if diff["shared_total"] else 0
        print(f"{diff['changed_count']} of {diff['shared_total']} shared transactions "
              f"({pct:.2f}%) changed on at least one of {TRACKED_COLUMNS}:")
        for col in TRACKED_COLUMNS:
            rows = diff["transitions"][col]
            if not rows:
                continue
            print(f"\n  {col}:")
            for r in rows[:15]:
                print(f"    {r['before']:>28} -> {r['after']:<28} x{r['count']}")
            if len(rows) > 15:
                print(f"    ... {len(rows) - 15} more transition type(s)")

        print(f"\n  Changed transaction_ids (first 20 of {diff['changed_count']}):")
        print(f"    {diff['changed_transaction_ids'][:20]}")

    print()
    print("Amount in question (exception rows only, ledger_expected_net_rupees):")
    ab, aa = diff["amount_before"], diff["amount_after"]
    print(f"  before: {ab['exception_count']} rows, sum Rs.{ab['sum_rupees']:,.2f}, "
          f"mean Rs.{ab['mean_rupees']:,.2f}")
    print(f"  after:  {aa['exception_count']} rows, sum Rs.{aa['sum_rupees']:,.2f}, "
          f"mean Rs.{aa['mean_rupees']:,.2f}")
    print(f"  delta:  {aa['exception_count'] - ab['exception_count']:+d} rows, "
          f"Rs.{aa['sum_rupees'] - ab['sum_rupees']:+,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before-ref", default=None,
                         help="git ref for the BEFORE side's code (default: HEAD). "
                              "Ignored if --before-dir/--after-dir are used.")
    parser.add_argument("--after-ref", default=None,
                         help="git ref for the AFTER side's code (default: the current "
                              "working tree, in-process, including uncommitted edits).")
    parser.add_argument("--before-dir", default=None,
                         help="data directory for the BEFORE side (data-diff mode).")
    parser.add_argument("--after-dir", default=None,
                         help="data directory for the AFTER side (data-diff mode).")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                         help="dataset both code versions run against, in ref-diff mode "
                              "(default: data/).")
    args = parser.parse_args()

    data_diff_mode = args.before_dir is not None or args.after_dir is not None
    if data_diff_mode:
        if not (args.before_dir and args.after_dir):
            print("ERROR: --before-dir and --after-dir must both be given together.")
            raise SystemExit(1)
        if args.before_ref or args.after_ref:
            print("ERROR: --before-ref/--after-ref cannot be combined with --before-dir/--after-dir "
                  "-- pick one axis to diff (code OR data), not both at once.")
            raise SystemExit(1)
        print(f"Comparing dataset {args.before_dir!r} vs {args.after_dir!r} under the CURRENT code.\n")
        before = _run_report_in_process(args.before_dir)
        after = _run_report_in_process(args.after_dir)
        before_label, after_label = args.before_dir, args.after_dir
    else:
        before_ref = args.before_ref or "HEAD"
        print(f"Comparing code {before_ref!r} vs "
              f"{args.after_ref or 'working tree (uncommitted edits included)'}, "
              f"both against data {args.data_dir!r}.\n")
        before = _get_report(ref=before_ref, data_dir=args.data_dir)
        after = _get_report(ref=args.after_ref, data_dir=args.data_dir)
        before_label = before_ref
        after_label = args.after_ref or "working tree"

    diff = diff_reports(before, after)
    print_diff_report(diff, before_label, after_label)


if __name__ == "__main__":
    main()
