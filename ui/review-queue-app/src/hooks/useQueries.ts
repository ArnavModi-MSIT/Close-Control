import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api";
import type { BulkReviewRequest, CaseFilters, ReviewSubmission } from "../types";

// Lightweight, cheap query -- polled unconditionally so the app can
// detect stream_mode turning on (see run_stream_simulator.py) and light
// up the live indicator, and so the KPI cards/charts update on their own
// while a stream is running without the user needing to touch anything.
// Harmless overhead against the static demo database too: a few KB every
// few seconds, no visible re-render since the numbers just aren't
// changing there.
const STATS_POLL_MS = 3000;

export function useStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: api.getStats,
    refetchInterval: STATS_POLL_MS,
    // React Query's default pauses polling in a backgrounded/hidden tab
    // (sensible in general -- don't burn requests on a tab nobody's
    // looking at). But this specific query is how the app detects
    // stream_mode and drives the live KPIs during a demo recording, where
    // the presenting window manager or capture software can sometimes
    // report the tab as "hidden" even while it's genuinely on screen.
    // Verified directly: this tool's own automated tab reports
    // document.hidden === true even when explicitly brought to front,
    // which is what surfaced this in the first place. Keep polling
    // regardless for this one query.
    refetchIntervalInBackground: true,
  });
}

export function useCases(
  filters: CaseFilters,
  sort: string,
  sortDirection: "asc" | "desc",
  page: number,
  pageSize: number,
  pollWhileStreaming: boolean,
) {
  return useQuery({
    queryKey: ["cases", filters, sort, sortDirection, page, pageSize],
    queryFn: () => api.listCases(filters, sort, sortDirection, page, pageSize),
    placeholderData: (prev) => prev, // keep the old table visible while a new filter/page loads
    // Only poll the case list/table when we know we're against a live
    // stream -- against the static demo database this would just be
    // wasted requests, since nothing changes there without a human
    // clicking a review decision (which already invalidates directly).
    refetchInterval: pollWhileStreaming ? STATS_POLL_MS : false,
    refetchIntervalInBackground: pollWhileStreaming,
  });
}

export function useReconciliationStatement(enabled: boolean) {
  return useQuery({
    queryKey: ["reconciliation-statement"],
    queryFn: api.getReconciliationStatement,
    enabled,
    // Not polled -- this is a report a reviewer opens deliberately, not a
    // live-updating widget; React Query still caches it so re-expanding
    // the panel doesn't re-fetch every time within the staleTime window.
    staleTime: 30_000,
  });
}

export function useCaseDetail(transactionId: string | null) {
  return useQuery({
    queryKey: ["case", transactionId],
    queryFn: () => api.getCase(transactionId!),
    enabled: transactionId !== null,
  });
}

export function useRunSummary(enabled: boolean) {
  return useQuery({
    queryKey: ["run-summary"],
    queryFn: api.getRunSummary,
    enabled,
    // A pre-generated file (run_summary.py's output), not a live
    // computation -- long staleTime is correct here, not a shortcut. It
    // only changes when someone re-runs that script.
    staleTime: 5 * 60_000,
  });
}

export function useMatcherAutoResolved(enabled: boolean, exceptionType?: string) {
  return useQuery({
    queryKey: ["matcher-auto-resolved", exceptionType ?? "all"],
    queryFn: () => api.getMatcherAutoResolved(exceptionType),
    enabled,
    // Same reasoning as useRootCauseClusters below -- a panel opened
    // deliberately, matched loosely to the endpoint's own 8s server-side
    // cache TTL.
    staleTime: 8_000,
  });
}

export function useCorrections(enabled: boolean) {
  return useQuery({
    queryKey: ["corrections"],
    queryFn: api.getCorrections,
    enabled,
    // Not cached server-side (the endpoint reads a small file fresh every
    // call), so a short client staleTime just avoids a redundant refetch
    // on rapid re-opens of the same panel.
    staleTime: 5_000,
  });
}

export function useAuditChainVerification(enabled: boolean) {
  return useQuery({
    queryKey: ["audit-chain-verify"],
    queryFn: api.getAuditChainVerification,
    enabled,
    // Deliberately NOT cached, matching the endpoint's own contract --
    // re-deriving the answer on every open is the entire point of an
    // integrity check. staleTime: 0 (the default) so React Query
    // refetches on every mount/re-open rather than trusting a stale hit.
  });
}

export function useRootCauseClusters(enabled: boolean) {
  return useQuery({
    queryKey: ["root-cause-clusters"],
    queryFn: api.getRootCauseClusters,
    enabled,
    // Same reasoning as useReconciliationStatement -- a panel a reviewer
    // opens deliberately, not a live-updating widget. Matches the
    // endpoint's own 8s server-side cache TTL closely enough that
    // re-expanding the panel rarely triggers a redundant matcher run.
    staleTime: 8_000,
  });
}

export function useBulkReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: BulkReviewRequest) => api.bulkReview(payload),
    onSuccess: () => {
      // A bulk action can change status for many cases and the cluster
      // membership itself (a reviewed case's transaction still appears in
      // the matcher's escalated set until the underlying condition
      // actually resolves, but its review status changes) -- invalidate
      // broadly, same "correctness over micro-optimizing" call as
      // useSubmitReview above.
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["root-cause-clusters"] });
    },
  });
}

export function useAskQA() {
  // A plain mutation, no onSuccess invalidation -- a Q&A answer is
  // read-only by construction (qa_agent/loop.py's own system prompt: "you
  // are answering a question, not authorizing an action"), so it never
  // changes anything else this app has cached.
  return useMutation({
    mutationFn: (question: string) => api.askQA(question),
  });
}

export function useReverify() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dryRun: boolean) => api.reverify(dryRun),
    onSuccess: (data) => {
      // A dry run (payload.dry_run in the response) never writes anything
      // server-side -- nothing to invalidate. A real run only actually
      // changed state if it closed at least one case.
      if (!data.dry_run && data.closed.length > 0) {
        queryClient.invalidateQueries({ queryKey: ["cases"] });
        queryClient.invalidateQueries({ queryKey: ["stats"] });
        queryClient.invalidateQueries({ queryKey: ["root-cause-clusters"] });
      }
    },
  });
}

export function useSubmitReview(transactionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ReviewSubmission) => api.submitReview(transactionId, payload),
    onSuccess: () => {
      // Every field a submitted review can affect: this case's own detail,
      // its row in the list (status/pill), and the aggregate stats/charts.
      // Invalidating broadly here is deliberate -- correctness over
      // micro-optimizing which queries strictly needed to change, for a
      // local tool where a refetch costs single-digit milliseconds.
      queryClient.invalidateQueries({ queryKey: ["case", transactionId] });
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
