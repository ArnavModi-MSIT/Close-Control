import type { CaseFilters, CaseStatus } from "../types";

const STATUS_OPTIONS: Array<{ value: CaseStatus | ""; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "auto_resolved", label: "AI auto-resolved" },
  { value: "pending", label: "Pending" },
  { value: "pending_manager_approval", label: "Awaiting manager" },
  { value: "approved", label: "Approved" },
  { value: "overridden", label: "Overridden" },
  { value: "escalated", label: "Escalated" },
  { value: "auto_closed", label: "Auto-closed (re-verified)" },
];

const SORT_OPTIONS = [
  { value: "amount_at_risk_rupees:desc", label: "Amount, high → low" },
  { value: "amount_at_risk_rupees:asc", label: "Amount, low → high" },
  { value: "agent_confidence:desc", label: "Confidence, high → low" },
  { value: "agent_confidence:asc", label: "Confidence, low → high" },
  { value: "transaction_id:asc", label: "Transaction ID" },
];

const selectCls =
  "rounded-lg border-[1.5px] border-border-2 bg-surface px-2.5 py-2 text-[0.85rem] text-ink";

interface Props {
  filters: CaseFilters;
  onFiltersChange: (f: CaseFilters) => void;
  exceptionTypes: string[];
  sort: string;
  onSortChange: (sort: string) => void;
}

export function FilterBar({ filters, onFiltersChange, exceptionTypes, sort, onSortChange }: Props) {
  const set = (patch: Partial<CaseFilters>) => onFiltersChange({ ...filters, ...patch });

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2.5">
      <div className="flex min-w-[180px] flex-1 items-center gap-2 rounded-lg border-[1.5px] border-border-2 bg-surface px-3 focus-within:border-accent">
        <svg aria-hidden viewBox="0 0 20 20" width="15" height="15" className="text-ink-mute">
          <circle cx="8.5" cy="8.5" r="6" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <line x1="13" y1="13" x2="18" y2="18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input
          type="search"
          placeholder="Search by transaction ID…"
          aria-label="Search by transaction ID"
          value={filters.search}
          onChange={(e) => set({ search: e.target.value })}
          className="w-full border-none bg-transparent py-2 text-[0.86rem] text-ink outline-none placeholder:text-ink-mute"
        />
      </div>

      <select
        className={selectCls}
        value={filters.status}
        onChange={(e) => set({ status: e.target.value as CaseStatus | "" })}
      >
        {STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      <select
        className={selectCls}
        value={filters.exception_type}
        onChange={(e) => set({ exception_type: e.target.value })}
      >
        <option value="">All exception types</option>
        {exceptionTypes.map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>

      <input
        type="number"
        placeholder="Min ₹"
        className={`${selectCls} w-[110px]`}
        value={filters.min_amount}
        onChange={(e) => set({ min_amount: e.target.value })}
      />
      <input
        type="number"
        placeholder="Max ₹"
        className={`${selectCls} w-[110px]`}
        value={filters.max_amount}
        onChange={(e) => set({ max_amount: e.target.value })}
      />

      <select className={selectCls} value={sort} onChange={(e) => onSortChange(e.target.value)}>
        {SORT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
