import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { dateOnly } from '@/lib/format';
import { decimalText } from '@/lib/overview-format';
import type { Performance } from '@/types/overview-api';
import { performanceSeries } from './performance-series';
export function PerformanceChart({ items }: { items: Performance[] }) {
  const { points, keys } = performanceSeries(items);
  if (!keys.length)
    return (
      <p className="notice">표시 가능한 성과 index가 없습니다. PARTIAL 자료는 표에서 확인하세요.</p>
    );
  return (
    <div
      className="overview-performance-chart"
      role="img"
      aria-label={`조회한 ${items.length}개 기록의 저장된 성과 index. PARTIAL, 새 BASELINE, 연속성이 확인되지 않는 구간은 연결하지 않습니다.`}
    >
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={points} margin={{ left: 0, right: 20, top: 16, bottom: 8 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="row.periodEndAt"
            tickFormatter={(v) => dateOnly(String(v)).slice(5)}
            minTickGap={35}
          />
          <YAxis width={60} domain={['auto', 'auto']} />
          <Tooltip
            content={({ active, payload }) => {
              const row = payload?.[0]?.payload?.row as Performance | undefined;
              return active && row ? (
                <div className="chart-tooltip">
                  <span>
                    {dateOnly(row.periodEndAt)} KST · {row.performanceStatus}
                  </span>
                  <strong>{decimalText(row.performanceIndex)}</strong>
                </div>
              ) : null;
            }}
          />
          {keys.map((key) => (
            <Line
              key={key}
              dataKey={key}
              type="linear"
              stroke="var(--chart-1)"
              strokeWidth={2}
              connectNulls={false}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
