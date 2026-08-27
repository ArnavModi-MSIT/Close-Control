export function Header({ streamMode }: { streamMode: boolean }) {
  return (
    <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3.5">
      <a href="/showcase.html" className="flex items-center gap-2.5 text-ink no-underline">
        <span
          aria-hidden
          className="h-[22px] w-[22px] flex-shrink-0 rounded-md"
          style={{ background: "linear-gradient(135deg, #8B2560, #8B2560 40%, #0B8F2F)" }}
        />
        <span className="hidden text-[0.98rem] font-bold tracking-tight sm:inline">AI Finance Controller</span>
      </a>
      <div className="flex items-center gap-4">
        {streamMode && (
          <span
            className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 font-mono text-[0.72rem] font-semibold tracking-wide text-white uppercase"
            title="Replaying the existing synthetic dataset in time order against a separate database -- not a live Razorpay connection"
          >
            <span className="relative h-1.5 w-1.5 flex-shrink-0 rounded-full bg-white">
              <span className="absolute inset-[-4px] animate-ping rounded-full border-[1.5px] border-white motion-reduce:animate-none" />
            </span>
            Live simulation
          </span>
        )}
        <span className="flex items-center gap-1.5 font-mono text-[0.72rem] tracking-wide text-ink-soft uppercase">
          <span className="relative h-1.5 w-1.5 flex-shrink-0 rounded-full bg-good">
            <span className="absolute inset-[-4px] animate-ping rounded-full border-[1.5px] border-good motion-reduce:animate-none" />
          </span>
          Local &middot; no hosting
        </span>
        <a href="/showcase.html" className="text-sm text-ink-soft hover:text-accent">
          &larr; Dashboard
        </a>
      </div>
    </header>
  );
}
