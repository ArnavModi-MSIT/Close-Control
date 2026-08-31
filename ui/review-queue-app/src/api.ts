import type {
  BulkReviewRequest,
  BulkReviewResult,
  CaseDetail,
  CaseFilters,
  CaseListResponse,
  QAResult,
  ReconciliationStatement,
  ReviewSubmission,
  ReviewSubmissionResult,
  RootCauseClustersResponse,
  StatsResponse,
} from "./types";

const API = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message = Array.isArray(detail)
      ? detail.map((e: { msg: string }) => e.msg).join("; ")
      : typeof detail === "string"
        ? detail
        : `HTTP ${res.status}`;
    throw new ApiError(message, res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function getStats(): Promise<StatsResponse> {
  return fetchJson(`${API}/stats`);
}

export function getReconciliationStatement(): Promise<ReconciliationStatement> {
  return fetchJson(`${API}/reconciliation-statement`);
}

export function listCases(
  filters: CaseFilters,
  sort: string,
  sortDirection: "asc" | "desc",
  page: number,
  pageSize: number,
): Promise<CaseListResponse> {
  const p = new URLSearchParams();
  if (filters.status) p.set("status", filters.status);
  if (filters.exception_type) p.set("exception_type", filters.exception_type);
  if (filters.min_amount) p.set("min_amount", filters.min_amount);
  if (filters.max_amount) p.set("max_amount", filters.max_amount);
  if (filters.search) p.set("search", filters.search);
  p.set("sort", sort);
  p.set("sort_direction", sortDirection);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  return fetchJson(`${API}/cases?${p.toString()}`);
}

export function getCase(transactionId: string): Promise<CaseDetail> {
  return fetchJson(`${API}/cases/${encodeURIComponent(transactionId)}`);
}

export function submitReview(
  transactionId: string,
  payload: ReviewSubmission,
): Promise<ReviewSubmissionResult> {
  return fetchJson(`${API}/cases/${encodeURIComponent(transactionId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getRunSummary(): Promise<{ generated: boolean; summary: string | null }> {
  return fetchJson(`${API}/run-summary`);
}

export function getRootCauseClusters(): Promise<RootCauseClustersResponse> {
  return fetchJson(`${API}/root-cause-clusters`);
}

export function bulkReview(payload: BulkReviewRequest): Promise<BulkReviewResult> {
  return fetchJson(`${API}/cases/bulk-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// A real tool-calling LLM round trip, ~1-2 minutes end to end on local
// Ollama -- callers should show a loading state for far longer than any
// other request in this app, not treat a slow response as broken.
export function askQA(question: string): Promise<QAResult> {
  return fetchJson(`${API}/qa`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}
