import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { StatsResponse } from "../../types";
import { STATUS_COLORS, statusLabel } from "../../lib/format";

export function StatusDonut({ stats }: { stats: StatsResponse }) {
  const data = (Object.keys(stats.counts_by_status) as Array<keyof typeof stats.counts_by_status>)
    .map((k) => ({ key: k, name: statusLabel(k), value: stats.counts_by_status[k] }))
    .filter((d) => d.value > 0);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <h3 className="mb-3 text-[0.95rem] font-semibold">Status breakdown</h3>
      <div className="flex items-center gap-4">
        <div className="h-[160px] w-[160px] flex-shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" innerRadius={45} outerRadius={72} paddingAngle={2}>
                {data.map((d) => (
                  <Cell key={d.key} fill={STATUS_COLORS[d.key]} stroke="var(--color-surface)" strokeWidth={2} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value, name) => [`${value} cases`, String(name)]}
                contentStyle={{ fontSize: "0.8rem", borderRadius: 8, border: "1px solid var(--color-border)" }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <ul className="flex flex-1 flex-col gap-1.5 text-[0.82rem]">
          {data.map((d) => (
            <li key={d.key} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-ink-soft">
                <span className="h-2.5 w-2.5 flex-shrink-0 rounded-sm" style={{ background: STATUS_COLORS[d.key] }} />
                {d.name}
              </span>
              <span className="font-mono font-semibold">{d.value}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
