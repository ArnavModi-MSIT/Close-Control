export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-4 rounded-xl border border-crit/35 bg-crit-soft px-4 py-3 text-sm text-crit" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex-shrink-0 rounded-lg border-[1.5px] border-crit bg-surface px-3.5 py-1.5 text-sm font-semibold text-crit"
        >
          Retry
        </button>
      )}
    </div>
  );
}
