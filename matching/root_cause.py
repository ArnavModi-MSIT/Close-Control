"""Cross-case root-cause clustering: collapse a queue of individual
escalated CASES into the far smaller set of underlying PROBLEMS.

THE OPERATIONAL POINT
An analyst opening this queue sees 617 tickets. They are not 617 problems.
One settlement whose bank posting arrived without a UTR flags every payment
batched into it -- so a single upstream event can generate hundreds of
individually-escalated cases that all resolve the moment that one posting is
explained. On the curated dataset the worst case is `missing_bank_reference`:
497 escalated cases tracing to 21 settlements, a 23.7x amplification.
Working the queue case-by-case is therefore doing ~5x more work than the
data actually requires, and -- worse -- it hides that the 497 are ONE
recurring operational failure rather than 497 unrelated incidents.

WHY THIS IS DETERMINISTIC, NOT EMBEDDINGS
The obvious instinct is to embed each case's `root_cause` prose and cluster
semantically. That was considered and deliberately rejected: the cases that
share a root cause already share a real, exact join key (settlement_id),
because that is the literal mechanism by which one event fans out into many
cases (see data_generation/settlements.py's missing_utr_groups, which ORs
the missing-reference flag across an entire settlement group). An embedding
model would be approximating -- with less accuracy, a torch dependency, and
a model download -- a grouping that is already exactly computable. Same
reasoning as investigator/'s "deterministic pre-routing, not a trained
classifier": don't train a model to guess a boolean you can evaluate.

Semantic clustering would only earn its place if distinct settlements shared
a cause that no structural key captures. On this dataset they do not -- every
exception type other than missing_bank_reference sits at 1.0-1.3x, i.e.
already essentially one case per settlement, with nothing left to collapse.

This module is purely OBSERVATIONAL -- same contract as
matching/diagnostics.py. Nothing in the matching path imports it, and it
never changes a classification, a risk class, or an auto-resolve decision.
It reorganizes what the matcher already concluded; it never second-guesses it.
"""

import pandas as pd

def _amount_at_risk(group: pd.DataFrame) -> float:
    """Rupees this cluster puts in question. Uses the ledger's own expected
    net -- the amount the business believed it was owed -- rather than the
    observed net, because for an unmatched or missing posting the observed
    side is precisely what is not trustworthy."""
    return round(float(group["ledger_expected_net_rupees"].fillna(0).sum()), 2)


def cluster_escalated_cases(report: pd.DataFrame) -> pd.DataFrame:
    """Group every escalated case into its underlying root cause.

    Clustering key, in order of preference:
      1. (settlement_id, final_exception_type) -- the real fan-out mechanism
      2. (merchant_id, final_exception_type)   -- for cases with no settlement

    Returns one row per cluster, ordered by case_count descending, so the
    biggest lever is always first. Returns an empty frame with the right
    columns when there is nothing escalated, so callers never special-case.
    """
    columns = ["cluster_id", "cluster_key", "cluster_basis", "final_exception_type",
                "merchant_id", "settlement_id", "case_count", "risk_class",
                "amount_at_risk_rupees", "transaction_ids"]

    escalated = report[report["final_exception_type"].notna()
                        & (~report["auto_resolve_eligible"])].copy()
    if escalated.empty:
        return pd.DataFrame(columns=columns)

    # Cases carrying no settlement_id at all (held_for_risk_review never
    # reaches settlement) can't cluster on it, so they fall back to
    # (merchant_id, final_exception_type) instead -- kept explicit rather
    # than silently dropping them, since a case that cannot be grouped by
    # settlement is still a case an analyst has to work.
    has_settlement = escalated["settlement_id"].notna()
    escalated["cluster_basis"] = pd.Series(
        ["settlement" if s else "merchant" for s in has_settlement], index=escalated.index)
    escalated["_key_part"] = escalated["settlement_id"].where(
        has_settlement, escalated["merchant_id"])

    rows = []
    grouped = escalated.groupby(["cluster_basis", "_key_part", "final_exception_type"],
                                 dropna=False, sort=False)
    for (basis, key_part, exc_type), group in grouped:
        rows.append({
            "cluster_key": f"{basis}:{key_part}:{exc_type}",
            "cluster_basis": basis,
            "final_exception_type": exc_type,
            "merchant_id": group["merchant_id"].iloc[0],
            "settlement_id": key_part if basis == "settlement" else None,
            "case_count": int(len(group)),
            # Worst risk in the cluster -- a cluster is only as safe as its
            # most severe member, never an average.
            "risk_class": _worst_risk(group["risk_class"]),
            "amount_at_risk_rupees": _amount_at_risk(group),
            "transaction_ids": sorted(group["transaction_id"].tolist()),
        })

    clusters = pd.DataFrame(rows)
    clusters = clusters.sort_values(
        ["case_count", "amount_at_risk_rupees"], ascending=False).reset_index(drop=True)
    clusters.insert(0, "cluster_id", [f"rc-{i:04d}" for i in range(len(clusters))])
    _assert_partition(escalated, clusters)
    return clusters[columns]


def _assert_partition(escalated: pd.DataFrame, clusters: pd.DataFrame) -> None:
    """Every escalated case must appear in EXACTLY one cluster -- no case
    lost, no case counted twice. Raises rather than returning a flag, the
    same fail-loud contract as matching/diagnostics.py's invariant checks
    and ingestion/warehouse.py's identity assertion.

    This is not defensive padding: the headline claim this module exists to
    support ("617 cases are 130 real problems") is only true if the
    clustering is a genuine partition. A grouping bug that silently dropped
    cases would make the compression look BETTER while being wrong, and
    every other number here would still print happily.
    """
    clustered = [t for ids in clusters["transaction_ids"] for t in ids]
    expected = set(escalated["transaction_id"])

    if len(clustered) != len(expected):
        raise AssertionError(
            f"root-cause clustering is not a partition: {len(expected)} escalated cases in, "
            f"{len(clustered)} case slots across clusters out "
            f"({len(clustered) - len(set(clustered))} duplicated)."
        )
    if set(clustered) != expected:
        missing = sorted(expected - set(clustered))[:5]
        extra = sorted(set(clustered) - expected)[:5]
        raise AssertionError(
            f"root-cause clustering changed the case set -- missing: {missing}, unexpected: {extra}"
        )


_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _worst_risk(series: pd.Series) -> str:
    return max(series.dropna(), key=lambda r: _RISK_RANK.get(r, 0), default="none")


def summarize(clusters: pd.DataFrame, escalated_case_count: int) -> dict:
    """Headline numbers a dashboard or a reviewer actually wants: how much
    smaller the real problem set is than the ticket count implies."""
    if clusters.empty:
        return {"escalated_cases": escalated_case_count, "root_cause_clusters": 0,
                "amplification_factor": 0.0, "multi_case_clusters": 0,
                "cases_in_multi_case_clusters": 0, "largest_cluster_case_count": 0,
                "singleton_clusters": 0}

    multi = clusters[clusters["case_count"] > 1]
    return {
        "escalated_cases": int(escalated_case_count),
        "root_cause_clusters": int(len(clusters)),
        # How many cases the average cluster accounts for. The honest headline
        # is not this average but the concentration below it -- a handful of
        # clusters carry most of the queue.
        "amplification_factor": round(escalated_case_count / len(clusters), 2),
        "multi_case_clusters": int(len(multi)),
        "cases_in_multi_case_clusters": int(multi["case_count"].sum()),
        # The number that actually matters operationally: what share of the
        # queue is cleared by working only the clusters that fan out.
        # float(...) wrap: multi["case_count"].sum() is a numpy int64, and
        # round() on the resulting numpy float64 returns another numpy
        # scalar, not a plain Python float -- found live via qa_agent/'s
        # real Ollama test, whose tool-call trace showed
        # "np.float64(84.0)" leaking into a JSON-serialized tool result
        # (default=str papered over it as a string rather than a real
        # number, same incidental-not-a-fix protection this project has
        # already relied on and then closed properly elsewhere).
        "pct_cases_in_multi_case_clusters": round(
            100.0 * float(multi["case_count"].sum()) / escalated_case_count, 1),
        "singleton_clusters": int((clusters["case_count"] == 1).sum()),
        "largest_cluster_case_count": int(clusters["case_count"].max()),
        "largest_cluster_id": clusters.iloc[0]["cluster_id"],
        "largest_cluster_exception_type": clusters.iloc[0]["final_exception_type"],
        "total_amount_at_risk_rupees": round(float(clusters["amount_at_risk_rupees"].sum()), 2),
    }


def per_exception_type_amplification(report: pd.DataFrame, clusters: pd.DataFrame) -> pd.DataFrame:
    """Where the fan-out actually is. A type sitting at 1.0x is already one
    case per problem and clustering buys nothing there -- stating that
    explicitly matters, because a single blended average would imply the
    whole queue compresses evenly when in reality one type carries it."""
    escalated = report[report["final_exception_type"].notna()
                        & (~report["auto_resolve_eligible"])]
    if escalated.empty or clusters.empty:
        return pd.DataFrame(columns=["final_exception_type", "cases", "clusters", "amplification"])

    cases = escalated.groupby("final_exception_type").size()
    grouped = clusters.groupby("final_exception_type").size()
    out = pd.DataFrame({"cases": cases, "clusters": grouped}).fillna(0).astype(int)
    out["amplification"] = (out["cases"] / out["clusters"].replace(0, pd.NA)).round(2)
    return out.sort_values("cases", ascending=False).reset_index()
