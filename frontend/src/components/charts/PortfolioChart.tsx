import { useId, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Info } from 'lucide-react';
import { Change, EmptyState, Panel } from '@/components/common';
import { dateOnly, krw, toNumber } from '@/lib/format';
import type { PortfolioPerformancePoint } from '@/types/dashboard';
import { filterPerformancePoints, type ChartRange } from './performance-range';

export function PortfolioChart({
  points,
  asOf = points.at(-1)?.capturedAt ?? '',
}: {
  points: PortfolioPerformancePoint[];
  asOf?: string;
}) {
  const [range, setRange] = useState<ChartRange>('30');
  const id = useId().replace(/:/g, '');
  const filtered = filterPerformancePoints(points, range, asOf);
  const chartData = filtered.map((point) => ({
    date: point.capturedAt,
    value: toNumber(point.valueKrw),
  }));
  const first = chartData[0]?.value ?? null;
  const latest = chartData.at(-1)?.value ?? null;
  const change = first !== null && latest !== null ? latest - first : null;
  return (
    <Panel
      title="계좌 가치 추이"
      description="Portfolio value · KRW"
      action={
        <div className="chart-range" role="group" aria-label="차트 기간">
          {(['7', '30', '90', 'all'] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {value === 'all' ? '전체' : `${value}일`}
            </button>
          ))}
        </div>
      }
    >
      {filtered.length ? (
        <>
          <div className="chart-summary">
            <strong>{krw(latest)}</strong>
            <Change value={change}>{krw(change, true)}</Change>
            <span className="text-muted">선택 기간 가치 변화</span>
          </div>
          <div
            className="chart-wrap"
            role="img"
            aria-label={`계좌 가치 추이, ${filtered.length}개 시점, 마지막 가치 ${krw(latest)}. 결측 구간은 연결하지 않습니다.`}
          >
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <AreaChart data={chartData} margin={{ left: 0, right: 10, top: 8, bottom: 4 }}>
                <defs>
                  <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.18} />
                    <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.005} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 5" />
                <XAxis
                  dataKey="date"
                  axisLine={false}
                  tickLine={false}
                  minTickGap={38}
                  tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickMargin={14}
                  tickFormatter={(value) => dateOnly(String(value)).slice(5)}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  domain={['auto', 'auto']}
                  width={68}
                  tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickFormatter={(value) =>
                    `${(Number(value) / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}만`
                  }
                  tickCount={5}
                />
                <Tooltip
                  content={({ active, payload, label }) =>
                    active && payload?.length ? (
                      <div className="chart-tooltip">
                        <span className="text-muted">{dateOnly(String(label))} · KST</span>
                        <strong>
                          {krw(typeof payload[0].value === 'number' ? payload[0].value : null)}
                        </strong>
                      </div>
                    ) : null
                  }
                  cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 3' }}
                />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="var(--chart-1)"
                  strokeWidth={2.3}
                  fill={`url(#${id})`}
                  connectNulls={false}
                  isAnimationActive={false}
                  activeDot={{
                    r: 4,
                    fill: 'var(--chart-1)',
                    stroke: 'var(--surface)',
                    strokeWidth: 2,
                  }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="chart-caption">
            <Info size={12} />
            입출금을 포함한 계좌 가치입니다. 투자 수익률과 구분합니다. · {filtered.length}개 시점
          </p>
        </>
      ) : (
        <EmptyState
          title="선택한 기간의 자산 데이터가 없습니다"
          description="다른 기간을 선택해 확인해 주세요."
        />
      )}
    </Panel>
  );
}
