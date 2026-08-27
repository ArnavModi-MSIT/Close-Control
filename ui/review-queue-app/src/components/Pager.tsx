interface Props {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

export function Pager({ total, page, pageSize, onPageChange }: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-3 text-[0.82rem] text-ink-soft">
      <span>{total === 0 ? "0 cases" : `Page ${page} of ${pages} (${total} cases)`}</span>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="rounded-lg border border-border-2 bg-surface px-3 py-1.5 text-[0.82rem] disabled:opacity-40"
        >
          &larr; Prev
        </button>
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
          className="rounded-lg border border-border-2 bg-surface px-3 py-1.5 text-[0.82rem] disabled:opacity-40"
        >
          Next &rarr;
        </button>
      </div>
    </div>
  );
}
