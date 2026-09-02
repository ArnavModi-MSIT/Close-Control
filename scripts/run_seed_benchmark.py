"""
Accuracy across many seeds, not one.

The existing seed-robustness check (RNG_SEED_OVERRIDE + generate_data.py
--out-dir, evaluate.py --data-dir) proves the matcher holds up on ONE
alternate seed (1337). That's a real check, but still one data point --
the corpus and the engine were tuned by the same person, possibly against
each other's quirks, on the same dataset. This regenerates the whole
dataset from scratch for N independent seeds and reports the DISTRIBUTION
-- mean/min/max/stdev -- of every headline metric, plus every seed that
scored below a perfect run, by name, so a genuine regression is visible
even if it doesn't happen to land on seed 1337 specifically.

Each seed's data generation runs in its own subprocess, deliberately:
data_generation/config.py reads RNG_SEED_OVERRIDE once, at import time
(confirmed by reading the actual code, not assumed), so mutating the env
var and re-importing within one long-running process would silently keep
regenerating the FIRST seed's data forever. Scoring itself reuses
evaluate.py's own evaluate() function directly, by mutating its DATA_DIR
module attribute before each call -- the same sanctioned ground-truth
reader every other script in this project uses (see evaluate.py's own
module docstring and CLAUDE.md's "Ground truth is sacred" rule), not a
second hand-copied one. evaluate() has no file-write side effects of its
own (only its __main__ block writes a manifest), so calling it in a loop
is safe.

A per-seed failure (a real, unexpected exception -- including
run_matcher.py's own MatcherInvariantError, see run_matcher.py's module
docstring) is recorded as a failed row and the benchmark continues,
rather than crashing the whole run on one bad seed -- that failure IS the
finding this script exists to surface, not something to hide by aborting
before it's logged.

    python scripts/run_seed_benchmark.py                    # 25 seeds, starting at 1000
    python scripts/run_seed_benchmark.py --seeds 10 --start 5000
"""

import argparse
import contextlib
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import evaluate as _evaluate  # noqa: E402

OUT_PATH = os.path.join(_REPO_ROOT, "data", "seed_benchmark.json")

# Deliberately far from 42 (this project's default RNG_SEED) and 1337 (the
# existing documented alternate-seed check) so a default run here never
# accidentally re-scores either of those two.
DEFAULT_START_SEED = 1000

METRICS = (
    "settlement_aware_accuracy_pct",
    "false_auto_resolve_rate_pct",
    "auto_resolve_precision_pct",
    "auto_resolve_coverage_pct",
    "matcher_txns_per_second",
)


def _generate_seed(seed: int, out_dir: str) -> None:
    """Runs generate_data.py in its own fresh subprocess -- RNG_SEED_OVERRIDE
    must be read at process start, not mutated mid-process (see this
    module's own docstring)."""
    env = {**os.environ, "RNG_SEED_OVERRIDE": str(seed)}
    result = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, "generate_data.py"), "--out-dir", out_dir],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"generate_data.py failed for seed {seed}:\n{result.stderr[-2000:]}")


def _score_seed(data_dir: str) -> dict:
    """Reuses evaluate.py's own evaluate() unchanged. Output is suppressed
    (redirected, not printed) since this runs once per seed here; only the
    returned dict is kept -- the same headline numbers this project's own
    docs and dashboards already trust."""
    _evaluate.DATA_DIR = data_dir
    with contextlib.redirect_stdout(io.StringIO()):
        results = _evaluate.evaluate()
    row = {m: results.get(m) for m in METRICS}
    row["hard_negatives_total"] = results.get("hard_negatives_total")
    row["hard_negatives_correct"] = results.get("hard_negatives_correct")
    row["consumption_invariant_ok"] = results.get("consumption_invariants", {}).get("consumption_invariant_ok")
    row["conservation_mismatches"] = len(
        results.get("settlement_conservation", {}).get("exact_or_split_pass_with_real_delta", []))
    row["auto_resolvable_modes_consistent"] = results.get("auto_resolvable_modes_consistent")
    return row


def _is_perfect(row: dict) -> bool:
    """Deliberately does NOT require false_auto_resolve_rate_pct == 0 --
    a small nonzero rate is this project's own real, accepted, documented
    behavior on the default seed (0.72%), not a defect; treating it as a
    "perfect run" gate would flag the project's own real baseline as
    imperfect on every single seed, including the default one. The rate is
    still fully reported in the distribution below (mean/min/max/stdev) --
    only excluded from this specific accept/reject line, not hidden."""
    return (
        row.get("error") is None
        and row["settlement_aware_accuracy_pct"] == 100.0
        and bool(row["consumption_invariant_ok"])
        and row["conservation_mismatches"] == 0
        and row["hard_negatives_correct"] == row["hard_negatives_total"]
        and bool(row["auto_resolvable_modes_consistent"])
    )


def one_seed(seed: int) -> dict:
    t0 = time.perf_counter()
    row = {"seed": seed, "error": None}
    try:
        with tempfile.TemporaryDirectory(prefix=f"seed_bench_{seed}_") as tmp:
            _generate_seed(seed, tmp)
            row.update(_score_seed(tmp))
    except Exception as e:  # noqa: BLE001 -- deliberate: a bad seed is a finding, not a crash
        row["error"] = f"{type(e).__name__}: {e}"
        for m in METRICS:
            row.setdefault(m, None)
        row.setdefault("hard_negatives_total", None)
        row.setdefault("hard_negatives_correct", None)
        row.setdefault("consumption_invariant_ok", False)
        row.setdefault("conservation_mismatches", None)
        row.setdefault("auto_resolvable_modes_consistent", None)
    row["wall_seconds"] = round(time.perf_counter() - t0, 2)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=25, help="number of independent seeds to run")
    ap.add_argument("--start", type=int, default=DEFAULT_START_SEED, help="first seed value")
    args = ap.parse_args()

    rows = []
    for i in range(args.seeds):
        seed = args.start + i
        row = one_seed(seed)
        rows.append(row)
        if row["error"]:
            print(f"  seed {seed:<6} FAILED -- {row['error']}  ({row['wall_seconds']}s)", flush=True)
        else:
            print(f"  seed {seed:<6} accuracy={row['settlement_aware_accuracy_pct']:.1f}%  "
                  f"false_auto={row['false_auto_resolve_rate_pct']:.2f}%  "
                  f"hard_neg={row['hard_negatives_correct']}/{row['hard_negatives_total']}  "
                  f"invariants_ok={row['consumption_invariant_ok'] and row['conservation_mismatches'] == 0}  "
                  f"({row['wall_seconds']}s)", flush=True)

    def stat(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return {"mean": None, "min": None, "max": None, "stdev": None}
        return {
            "mean": round(statistics.fmean(vals), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "stdev": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        }

    summary = {m: stat(m) for m in METRICS}
    imperfect = [r for r in rows if not _is_perfect(r)]
    failed = [r for r in rows if r["error"]]

    print()
    print(f"{args.seeds} independent seeds ({args.start}-{args.start + args.seeds - 1})")
    print(f"{'metric':32} {'mean':>9} {'min':>9} {'max':>9} {'stdev':>9}")
    for m, v in summary.items():
        mean = "n/a" if v["mean"] is None else v["mean"]
        vmin = "n/a" if v["min"] is None else v["min"]
        vmax = "n/a" if v["max"] is None else v["max"]
        vstdev = "n/a" if v["stdev"] is None else v["stdev"]
        print(f"{m:32} {mean!s:>9} {vmin!s:>9} {vmax!s:>9} {vstdev!s:>9}")
    print()
    print(f"Seeds scoring below a perfect run: {len(imperfect)} of {args.seeds} "
          f"({len(failed)} outright failed)")
    for r in imperfect:
        if r["error"]:
            print(f"  seed {r['seed']}: FAILED -- {r['error']}")
        else:
            print(f"  seed {r['seed']}: accuracy={r['settlement_aware_accuracy_pct']}% "
                  f"false_auto={r['false_auto_resolve_rate_pct']}% "
                  f"hard_neg={r['hard_negatives_correct']}/{r['hard_negatives_total']} "
                  f"invariants_ok={r['consumption_invariant_ok'] and r['conservation_mismatches'] == 0} "
                  f"auto_resolvable_modes_consistent={r['auto_resolvable_modes_consistent']}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"seeds": args.seeds, "start": args.start, "summary": summary,
                    "imperfect_seeds": imperfect, "rows": rows}, f, indent=2)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
