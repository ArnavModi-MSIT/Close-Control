import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { StatsResponse } from "../../types";

export function InvestigationDonut({ stats }: { stats: StatsResponse }) {
  const notYet = stats.total_cases - stats.investigated_count;
  const data = [
    { key: "investigated", name: "Investigated", value: stats.investigated_count, fill: "var(--color-accent)" },
    { key: "not_yet", name: "Not yet investigated", value: notYet, fill: "var(--color-accent-soft)" },
  ].filter((d) => d.value > 0);

  const pct = stats.total_cases > 0 ? Math.round((stats.investigated_count / stats.total_cases) * 100) : 0;

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <h3 className="mb-3 text-[0.95rem] font-semibold">Investigation coverage</h3>
      <div className="flex items-center gap-4">
        <div className="relative h-[160px] w-[160px] flex-shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" innerRadius={45} outerRadius={72} paddingAngle={2}>
                {data.map((d) => (
                  <Cell key={d.key} fill={d.fill} stroke="var(--color-surface)" strokeWidth={2} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value, name) => [`${value} cases`, String(name)]}
                contentStyle={{ fontSize: "0.8rem", borderRadius: 8, border: "1px solid var(--color-border)" }}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-[1.3rem] font-bold text-ink">{pct}%</span>
          </div>
        </div>
        <ul className="flex flex-1 flex-col gap-1.5 text-[0.82rem]">
          {data.map((d) => (
            <li key={d.key} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-ink-soft">
                <span className="h-2.5 w-2.5 flex-shrink-0 rounded-sm" style={{ background: d.fill }} />
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
