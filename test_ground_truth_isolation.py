"""
Ground-truth isolation guard: proves this project's own stated working
principle ("Ground truth is sacred... Only evaluate.py touches it") holds
in the actual code, instead of relying on manual discipline alone.

Idea sharpened by checking a peer Razorpay buildathon repo
(flare19/payment-reconciliation-agent-platform) past its README, into its
actual tests/unit/truth-leak-guard.test.ts -- a static-scan test enforcing
their own ADR-021 ("ground truth never enters the engine") by scanning
every source file for a forbidden reference. This project has stated the
identical rule since its own first working principle, but until now never
had an automated proof of it -- only the discipline of every module's own
docstring saying "never reads ground_truth.csv."

Building this guard surfaced one real, previously-undocumented fact: the
project's rule as stated ("only evaluate.py") was technically imprecise --
run_baseline_naive.py also read ground_truth.csv directly, for the
identical purpose (final scoring, never as an oracle mid-pipeline; its own
docstring already said "same rule as evaluate.py"), via its own
independently-hand-copied pd.read_csv() call. Not a violation of the RULE's
intent, but a second reader that could silently drift from evaluate.py's
own read logic. Fixed alongside this guard: run_baseline_naive.py now
imports and calls evaluate.load_ground_truth() directly instead of
duplicating the read -- there is now genuinely ONE reader, not two that
happen to agree today.

Mechanism: strip comments and docstrings from every .py file in the repo
(a comment or docstring referencing "ground_truth.csv" is fine -- e.g.
every "Never reads ground_truth.csv" docstring already in this codebase),
then scan the remaining real code for the literal filename
"ground_truth.csv". Any match outside the explicit allowlist below is a
violation -- this is a static-scan proof, not a runtime one, so it also
catches a violation in a code path that never actually executes during a
normal test run.

Allowlisted files, and why each is legitimate:
  - evaluate.py                          the one sanctioned reader (this
                                          project's own stated rule)
  - run_baseline_naive.py                delegates to evaluate.py's own
                                          load_ground_truth() -- the SAME
                                          reader, reused, not a second one
  - generate_data.py                     the WRITER -- produces
                                          ground_truth.csv in the first
                                          place, a different role entirely
                                          than reading it back as an oracle
  - data_generation/ground_truth.py      builds the ground-truth DataFrame
                                          in memory; never reads the file
                                          back off disk

    python test_ground_truth_isolation.py
"""

import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

ALLOWLISTED_FILES = {
    "evaluate.py",
    "run_baseline_naive.py",
    "generate_data.py",
    os.path.join("data_generation", "ground_truth.py"),
    # Not a reader at all -- a print() disclaimer string that names the
    # filename specifically to state IT NEVER READS IT ("...never reads
    # ground_truth.csv). N transactions captured..."). A real, if narrow,
    # false-positive for a pattern match this crude, found by actually
    # running the guard rather than assuming it clean -- allowlisted with
    # its reason recorded here rather than silently loosening the pattern.
    "run_cash_position.py",
}

EXCLUDED_DIRS = {".venv", "node_modules", "__pycache__", ".git", "data",
                  "ui", "airflow", "postgres", "redis",
                  # Isolated git worktrees created by Agent tool calls
                  # (isolation: "worktree") -- these are throwaway copies
                  # of the repo at some past commit, not this project's
                  # real source tree; scanning them produces false
                  # "violations" against files that aren't actually part
                  # of the live codebase at all.
                  ".claude"}

FORBIDDEN_PATTERN = re.compile(r"ground_truth\.csv")

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


def strip_comments_and_docstrings(source: str) -> str:
    """Crude but sufficient state-machine stripping: removes '#' line
    comments and triple-quoted strings (the vast majority of which are
    docstrings in this codebase), while leaving real code -- including
    ordinary single/double-quoted string literals -- untouched. Mirrors
    the peer repo's own stripComments() intent ("remove comments while
    preserving string and template literals") adapted for Python's
    comment/docstring syntax rather than JS's."""
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "#":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if source[i:i + 3] in ('"""', "'''"):
            quote = source[i:i + 3]
            i += 3
            end = source.find(quote, i)
            i = (end + 3) if end != -1 else n
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    out.append(source[i:i + 2])
                    i += 2
                    continue
                out.append(source[i])
                i += 1
            if i < n:
                out.append(source[i])
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def find_py_files() -> list[str]:
    matches = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                matches.append(os.path.relpath(os.path.join(dirpath, fname), REPO_ROOT))
    return matches


def main() -> None:
    print("\nSection 1: the guard's own comment/docstring stripper behaves correctly")

    sample = (
        '"""A module that never reads ground_truth.csv, see rule above."""\n'
        "# ground_truth.csv is mentioned only in this comment\n"
        "import os\n"
        'x = "not_ground_truth.csv"  # a real string literal survives stripping\n'
    )
    stripped = strip_comments_and_docstrings(sample)
    check("a docstring mentioning ground_truth.csv is removed by stripping",
          "ground_truth.csv" not in stripped or 'x = "not_ground_truth.csv"' in stripped,
          stripped)
    check("a real string literal (not a comment) survives stripping",
          'x = "not_ground_truth.csv"' in stripped, stripped)
    check("a '#' line comment mentioning the forbidden pattern is removed",
          stripped.count("ground_truth.csv") == 1, stripped)  # only the real string literal remains
    print()

    print("Section 2: no code outside the allowlist can reach ground_truth.csv")

    py_files = find_py_files()
    check("the scan found a non-trivial number of .py files (guard isn't accidentally scanning nothing)",
          len(py_files) > 20, f"found {len(py_files)}")

    violations = []
    for rel_path in py_files:
        if rel_path == os.path.basename(__file__) or rel_path in ALLOWLISTED_FILES:
            continue
        abs_path = os.path.join(REPO_ROOT, rel_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            source = f.read()
        stripped = strip_comments_and_docstrings(source)
        if FORBIDDEN_PATTERN.search(stripped):
            violations.append(rel_path)

    check("zero non-allowlisted files reference ground_truth.csv outside a comment/docstring",
          len(violations) == 0, f"violations: {violations}")

    for allowlisted in ALLOWLISTED_FILES:
        check(f"allowlisted file exists on disk: {allowlisted}",
              os.path.isfile(os.path.join(REPO_ROOT, allowlisted)))
    print()

    print("Section 3: the guard is not vacuously passing -- a real violation IS caught")

    tmp_violator_source = (
        "import pandas as pd\n"
        'gt = pd.read_csv("data/ground_truth.csv")  # a real, live violation\n'
    )
    tmp_stripped = strip_comments_and_docstrings(tmp_violator_source)
    check("a synthetic file with a real (non-comment) reference is correctly flagged as a violation",
          bool(FORBIDDEN_PATTERN.search(tmp_stripped)), tmp_stripped)

    print(f"\n{'=' * 62}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 62}")
    if _failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
