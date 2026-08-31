import { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { KpiCards } from "./components/KpiCards";
import { StatusDonut } from "./components/charts/StatusDonut";
import { ExceptionTypeBar } from "./components/charts/ExceptionTypeBar";
import { InvestigationDonut } from "./components/charts/InvestigationDonut";
import { ToolsHub } from "./components/ToolsHub";
import { FilterBar } from "./components/FilterBar";
import { CaseTable } from "./components/CaseTable";
import { Pager } from "./components/Pager";
import { DetailPanel } from "./components/DetailPanel";
import { ErrorBanner } from "./components/ui/ErrorBanner";
import { useCases, useStats } from "./hooks/useQueries";
import type { CaseFilters } from "./types";

const PAGE_SIZE = 15;

export default function App() {
  const [filters, setFilters] = useState<CaseFilters>({
    status: "", exception_type: "", min_amount: "", max_amount: "", search: "",
  });
  const [sortValue, setSortValue] = useState("amount_at_risk_rupees:desc");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [knownExceptionTypes, setKnownExceptionTypes] = useState<string[]>([]);

  const [sort, sortDirection] = sortValue.split(":") as [string, "asc" | "desc"];

  const stats = useStats();
  const streamMode = stats.data?.stream_mode ?? false;
  const cases = useCases(filters, sort, sortDirection, page, PAGE_SIZE, streamMode);

  // Same accumulation the old vanilla version did: the exception-type
  // filter's option list grows from whatever's actually been seen across
  // requests, rather than needing its own dedicated endpoint.
  useEffect(() => {
    if (!cases.data) return;
    setKnownExceptionTypes((prev) => {
      const next = new Set(prev);
      cases.data.items.forEach((c) => next.add(c.matcher_exception_type));
      return Array.from(next).sort();
    });
  }, [cases.data]);

  function updateFilters(f: CaseFilters) {
    setFilters(f);
    setPage(1);
  }

  return (
    <>
      <Header streamMode={streamMode} />
      <main className="mx-auto max-w-[1600px] px-6 py-8 pb-20">
        <div className="mb-6 flex flex-wrap items-baseline justify-between gap-4">
          <div>
            <h1 className="text-[1.7rem] font-bold">Reconciliation review queue</h1>
            <p className="mt-1 text-[0.92rem] text-ink-soft">
              Cases the deterministic gate escalated or auto-resolved — AI proposed, human
              disposes. Tier-2 cases (₹50,000+) need an analyst <em>and</em> a different manager
              to sign off.
            </p>
          </div>
        </div>

        {stats.isError && <ErrorBanner message="Couldn't load queue stats -- is the review server running?" onRetry={() => stats.refetch()} />}
        {cases.isError && <ErrorBanner message="Couldn't load the case list -- is the review server running?" onRetry={() => cases.refetch()} />}

        {stats.data && (
          <div className="mb-6 flex flex-col gap-4">
            <KpiCards stats={stats.data} streamMode={streamMode} />
            {/* min-w-0 on every grid child: a bare fr/1-col track is
                minmax(auto,1fr), so a child's intrinsic content (here, the
                bar chart's wide Y-axis labels) can force the grid track --
                and the whole page -- wider than the viewport. Same bug,
                same fix, as ui/styles.css's .queue-layout > * rule from
                the vanilla version; Tailwind just needs it spelled out
                per grid since there's no equivalent global default. */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="min-w-0"><StatusDonut stats={stats.data} /></div>
              <div className="min-w-0"><InvestigationDonut stats={stats.data} /></div>
              <div className="min-w-0"><ExceptionTypeBar stats={stats.data} /></div>
            </div>
          </div>
        )}

        <ToolsHub />

        <FilterBar
          filters={filters}
          onFiltersChange={updateFilters}
          exceptionTypes={knownExceptionTypes}
          sort={sortValue}
          onSortChange={(v) => { setSortValue(v); setPage(1); }}
        />

        <div className="grid min-w-0 grid-cols-1 items-start gap-5 lg:grid-cols-[1.7fr_1fr]">
          <div className="min-w-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
            <div className="overflow-x-auto">
              <CaseTable items={cases.data?.items ?? []} selected={selected} onSelect={setSelected} />
            </div>
            <Pager
              total={cases.data?.total ?? 0}
              page={cases.data?.page ?? 1}
              pageSize={cases.data?.page_size ?? PAGE_SIZE}
              onPageChange={setPage}
            />
          </div>

          <div className="min-w-0">
            <DetailPanel transactionId={selected} />
          </div>
        </div>
      </main>
    </>
  );
}
