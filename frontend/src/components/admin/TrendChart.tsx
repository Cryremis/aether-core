import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export const TREND_PALETTE = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#ea580c"];

export type TrendSeries = {
  key: string;
  label: string;
  color: string;
  /** 双刻度轴: 消息类大量级指标挂右轴,用户/会话挂左轴,避免互相压扁 */
  axis?: "left" | "right";
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
  /** 堆叠模式: 各序列堆叠为面积(平台对比视图),全部挂左轴 */
  stacked?: boolean;
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

const AXIS_COLOR = "color-mix(in srgb, var(--text-primary) 10%, transparent)";

/**
 * 基于 recharts 的多序列面积趋势图:
 * - 双 Y 轴(axis 字段指定),大量级指标独立右轴
 * - 图例点击切换序列显隐
 * - 关闭动画与圆点(大数据量下交互跟手)
 */
export function TrendChart({ points, series, height = 260, formatValue, stacked = false }: TrendChartProps) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const fmt = formatValue ?? ((v: number) => (v >= 10000 ? `${(v / 10000).toFixed(1)}w` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v))));

  const data = useMemo(
    () => points.map((p) => ({ ...p, date: formatDateShort(p.date) })),
    [points],
  );

  if (points.length === 0 || series.length === 0) {
    return <div className="trend-chart trend-chart--empty">暂无趋势数据</div>;
  }

  const hasRight = !stacked && series.some((s) => s.axis === "right");

  return (
    <div className="trend-chart">
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: hasRight ? 8 : 12, bottom: 0, left: -8 }}>
          <defs>
            {series.map((s) => (
              <linearGradient key={s.key} id={`trend-fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={stacked ? 0.85 : 0.28} />
                <stop offset="100%" stopColor={s.color} stopOpacity={stacked ? 0.55 : 0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke={AXIS_COLOR} vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: AXIS_COLOR }}
            minTickGap={24}
          />
          <YAxis
            yAxisId="left"
            tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={52}
            tickFormatter={(v: number) => fmt(v)}
          />
          {hasRight ? (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(v: number) => fmt(v)}
            />
          ) : null}
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: "var(--text-primary)", fontWeight: 600, marginBottom: 4 }}
            itemStyle={{ padding: 0 }}
            formatter={(value, name) => [fmt(Number(value)), String(name)]}
          />
          {series.map((s) => (
            <Area
              key={s.key}
              yAxisId={s.axis === "right" && hasRight ? "right" : "left"}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stackId={stacked ? "stack" : undefined}
              stroke={s.color}
              strokeWidth={stacked ? 0.5 : 2}
              fill={`url(#trend-fill-${s.key})`}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-0)" }}
              isAnimationActive={false}
              hide={Boolean(hidden[s.key])}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
      <div className="trend-chart__legend">
        {series.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`trend-chart__legend-item ${hidden[s.key] ? "is-off" : ""}`}
            onClick={() => setHidden((current) => ({ ...current, [s.key]: !current[s.key] }))}
            title={hidden[s.key] ? "点击显示" : "点击隐藏"}
          >
            <i style={{ background: s.color }} />
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

type PlatformSeriesInput = {
  platform_id: number;
  display_name: string;
  points: {
    date: string;
    new_users: number;
    new_conversations: number;
    new_messages: number;
    total_users: number;
    total_conversations: number;
    total_messages: number;
  }[];
};

/**
 * 平台对比堆叠图: 把后端的 per-platform 时序合并为宽表,
 * 每个平台一条堆叠面积——分层色块即占比,堆叠高度即总量。
 */
export function PlatformStackedChart({
  series,
  metric,
  cumulative,
  height = 280,
}: {
  series: PlatformSeriesInput[];
  metric: "users" | "conversations" | "messages";
  cumulative: boolean;
  height?: number;
}) {
  const { points, chartSeries } = useMemo(() => {
    if (series.length === 0) return { points: [] as TrendPoint[], chartSeries: [] as TrendSeries[] };
    const valueKey = cumulative ? `total_${metric}` : `new_${metric}`;
    const dates = series[0].points.map((p) => p.date);
    const merged: TrendPoint[] = dates.map((date) => {
      const row: TrendPoint = { date };
      for (const s of series) {
        const hit = s.points.find((p) => p.date === date);
        row[`p${s.platform_id}`] = Number(hit?.[valueKey] ?? 0);
      }
      return row;
    });
    const chartSeries: TrendSeries[] = series.map((s, i) => ({
      key: `p${s.platform_id}`,
      label: s.display_name,
      color: TREND_PALETTE[i % TREND_PALETTE.length],
    }));
    return { points: merged, chartSeries };
  }, [series, metric, cumulative]);

  return <TrendChart points={points} series={chartSeries} stacked height={height} />;
}
