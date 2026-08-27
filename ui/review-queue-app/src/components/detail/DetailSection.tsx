import type { ReactNode } from "react";

export function DetailSection({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-5 last:mb-0">
      <h4 className="mb-2.5 flex items-center gap-2 text-[0.75rem] tracking-wide text-ink-soft uppercase">
        {title}
        {badge}
      </h4>
      {children}
    </div>
  );
}

export function KvRow({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-t border-border py-1.5 text-[0.86rem] first:border-t-0">
      <span className="text-ink-soft">{k}</span>
      <span className="font-mono text-right">{v}</span>
    </div>
  );
}
