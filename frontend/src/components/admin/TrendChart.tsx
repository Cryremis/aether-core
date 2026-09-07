import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type TrendSeries = {
  key: string;
  label: string;
  color: string;
};

export type TrendPoint = {
  date: string;
  [seriesKey: string]: number | string;
};

type TrendChartProps = {
  points: TrendPoint[];
  series: TrendSeries[];
  height?: number;
  formatValue?: (value: number) => string;
};

function formatDateShort(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${Number(month)}/${Number(day)}`;
}

const TOOLTIP_STYLE = {
  background: "var(--surface-0)",
  border: "1px solid var(--border-subtle)",
  borderRadius: 10,
  fontSize: 12,
  padding: "8px 12px",
  boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
} as const;

/**
 * 基于 recharts 的多序列面积趋势图。
 * 数值按 series.key 从 points 提取;明暗主题通过 CSS 变量适配。
 */
export function TrendChart({ points, series, height = 260, formatValue }: TrendChartProps) {
  const fmt = formatValue ?? ((v: number) => (v >= 10000 ? `${(v / 10000).toFixed(1)}w` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v))));

  const data = useMemo(
    () => points.map((p) => ({ ...p, date: formatDateShort(p.date) })),
    [points],
  );

  if (points.length === 0 || series.length === 0) {
    return <div className="trend-chart trend-chart--empty">暂无趋势数据</div>;
  }

  return (
    <div className="trend-chart">
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <defs>
            {series.map((s) => (
              <linearGradient key={s.key} id={`trend-fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.28} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="color-mix(in srgb, var(--text-primary) 8%, transparent)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "color-mix(in srgb, var(--text-primary) 10%, transparent)" }}
            minTickGap={24}
          />
          <YAxis
            tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={52}
            tickFormatter={(v: number) => fmt(v)}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: "var(--text-primary)", fontWeight: 600, marginBottom: 4 }}
            itemStyle={{ padding: 0 }}
            formatter={(value, name) => [fmt(Number(value)), String(name)]}
          />
          {series.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#trend-fill-${s.key})`}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-0)" }}
              connectNulls
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
      <div className="trend-chart__legend">
        {series.map((s) => (
          <span key={s.key} className="trend-chart__legend-item">
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
