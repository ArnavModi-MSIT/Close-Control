import type { ActivityItem } from "../../types";
import { actionLabel, formatTimestamp } from "../../lib/format";

export function ActivityTimeline({ activity }: { activity: ActivityItem[] }) {
  return (
    <div className="flex flex-col gap-2">
      {activity.map((a, i) => (
        <div
          key={`${a.timestamp ?? "no-ts"}-${a.actor}-${a.action}-${i}`}
          className={`rounded-lg border border-border border-l-[3px] bg-surface-2 py-2.5 pr-2.5 pl-3.5 text-[0.83rem] ${
            a.actor_type === "ai" ? "border-l-accent" : a.actor_type === "system" ? "border-l-warn" : "border-l-good"
          }`}
        >
          <div className="mb-1 flex justify-between gap-2.5 font-mono text-[0.72rem] text-ink-soft">
            <span className="font-semibold text-ink">{a.actor}</span>
            <span>{formatTimestamp(a.timestamp)}</span>
          </div>
          <div className="text-ink-soft">
            <span className="font-semibold text-ink capitalize">{actionLabel(a.action)}</span> — {a.detail}
          </div>
        </div>
      ))}
    </div>
  );
}
