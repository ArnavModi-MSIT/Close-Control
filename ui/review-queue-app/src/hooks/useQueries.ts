import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api";
import type { CaseFilters, ReviewSubmission } from "../types";

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
