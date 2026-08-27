import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { StatsResponse } from "../../types";

export function ExceptionTypeBar({ stats }: { stats: StatsResponse }) {
  const data = stats.exception_type_breakdown.slice(0, 8).map((d) => ({
    ...d,
    label: d.exception_type.replace(/_/g, " "),
    not_investigated_count: d.count - d.investigated_count,
  }));

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <h3 className="mb-3 text-[0.95rem] font-semibold">Exception types</h3>
      <div className="h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="var(--color-border)" />
            <XAxis type="number" tick={{ fontSize: 11, fill: "var(--color-ink-soft)" }} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="label"
              width={140}
              tick={{ fontSize: 11, fill: "var(--color-ink-soft)" }}
            />
            <Tooltip
              formatter={(value, name) =>
                [`${value} cases`, name === "investigated_count" ? "Investigated" : "Not yet investigated"]
              }
              contentStyle={{ fontSize: "0.8rem", borderRadius: 8, border: "1px solid var(--color-border)" }}
            />
            <Legend
              formatter={(value) => (value === "investigated_count" ? "Investigated" : "Not yet investigated")}
              wrapperStyle={{ fontSize: "0.76rem" }}
            />
            <Bar dataKey="investigated_count" stackId="a" fill="var(--color-accent)" radius={[0, 0, 0, 0]} />
            <Bar dataKey="not_investigated_count" stackId="a" fill="var(--color-accent-soft)" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
