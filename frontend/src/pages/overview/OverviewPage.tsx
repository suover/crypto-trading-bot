import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Coins,
  FlaskConical,
  Info,
  ShieldCheck,
  Wallet,
} from 'lucide-react';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Change,
  CoinIdentity,
  EmptyState,
  InfoTip,
  MetricCard,
  Panel,
  SectionHeading,
  StatusBadge,
} from '@/components/common';
import { PortfolioChart } from '@/components/charts/PortfolioChart';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import { dateOnly, dateTime, krw, percent, quantity, ratio, toNumber } from '@/lib/format';

export default function OverviewPage() {
  const { data } = useDashboardData();
  if (!data) return null;
  const { summary, portfolio, research, analyses, operations } = data;
  const topPositions = portfolio.positions
    .filter((position) => position.asset !== 'KRW')
    .slice()
    .sort((a, b) => (toNumber(b.valueKrw) ?? -Infinity) - (toNumber(a.valueKrw) ?? -Infinity))
    .slice(0, 4);
  const latestGate = research.candidates
    .slice()
    .sort((a, b) => b.gate.evaluatedAt.localeCompare(a.gate.evaluatedAt))[0]?.gate;
  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="YOUR TRADING WORKSPACE"
        title="시장을 읽고, 흐름을 한눈에."
        description="계좌부터 AI의 판단, 전략 연구까지. 오늘의 데모 워크스페이스를 확인하세요."
        action={
          <span className="chip">
            <CalendarDays size={13} />
            {dateOnly(data.asOf)} · KST
          </span>
        }
      />
      <div className="grid-metrics">
        <MetricCard
          className="metric-highlight"
          label="총 계좌 평가금액"
          value={krw(summary.totalValueKrw)}
          icon={<Wallet />}
          trend={
            <Change value={toNumber(portfolio.summary.dailyChangeKrw)}>
              {krw(portfolio.summary.dailyChangeKrw, true)}
            </Change>
          }
          detail={
            portfolio.summary.valuationStatus === 'PARTIAL'
              ? '일부 자산 평가 불가'
              : '전일 대비 · 입출금 포함'
          }
        />
        <MetricCard
          label="계좌 수익률"
          value={<span className="positive">{percent(summary.accountReturnPercent, true)}</span>}
          icon={
            <InfoTip label="완료된 외부 현금흐름을 조정한 계좌 성과입니다. 보유 자산의 단순 평가손익 또는 봇 실현손익과 다릅니다." />
          }
          detail="Modified Dietz · 성과 기준점 이후"
        />
        <MetricCard
          label="봇 실현손익"
          value={krw(summary.botRealizedPnlKrw, true)}
          icon={<ArrowUpRight />}
          detail={
            <span>
              입증된 FIFO 매칭분 <StatusBadge status={portfolio.botPnl.accountingStatus} />
            </span>
          }
        />
        <MetricCard
          label="보유 코인"
          value={
            <>
              {summary.heldAssetCount}
              <span
                style={{ fontSize: 14, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 6 }}
              >
                assets
              </span>
            </>
          }
          icon={<Coins />}
          detail={`KRW 현금 · ${krw(portfolio.summary.cashKrw)}`}
        />
      </div>
      <div className="overview-main-grid">
        <PortfolioChart points={portfolio.performance} asOf={data.asOf} />
        <section className="card research-preview">
          <span className="eyebrow">STRATEGY LAB</span>
          <div className="section-inline">
            <h2>데이터로 검증하는 전략</h2>
            <FlaskConical size={20} className="text-primary" />
          </div>
          <p>
            후보 검증에서 Shadow 관찰까지.
            <br />
            현재 실행 정책은 별도의 승인 경계로 보호됩니다.
          </p>
          <div style={{ marginTop: 18 }}>
            <StatusBadge status={research.runtimePolicy.mode} />
            <span className="small text-muted" style={{ marginLeft: 8 }}>
              {summary.activePolicy}
            </span>
          </div>
          <div className="research-counts">
            <div>
              <strong>{summary.registeredCandidateCount}</strong>
              <span>등록 후보</span>
            </div>
            <div>
              <strong>{summary.shadowEnrollmentCount}</strong>
              <span>Shadow 등록</span>
            </div>
            <div>
              <strong>{summary.researchSnapshotCount.toLocaleString('ko-KR')}</strong>
              <span>연구 스냅샷</span>
            </div>
          </div>
          <div className="small text-muted" style={{ marginBottom: 16 }}>
            최근 Gate{' '}
            <span style={{ marginLeft: 8 }}>
              {latestGate ? <StatusBadge status={latestGate.status} /> : '—'}
            </span>
            <div style={{ marginTop: 8 }}>연구 갱신 · {dateTime(summary.latestResearchAt)}</div>
          </div>
          <Button variant="outline" asChild>
            <Link to="/research">
              전략 연구 살펴보기 <ArrowRight size={14} />
            </Link>
          </Button>
        </section>
      </div>
      <div className="overview-main-grid">
        <Panel
          title="최근 AI 판단"
          description="추천과 주문은 별도의 단계입니다"
          action={
            <Link className="text-button" to="/ai-analysis">
              전체 보기 <ArrowUpRight size={14} />
            </Link>
          }
        >
          {analyses.length ? (
            <div className="table-scroll">
              <table className="data-table">
                <caption className="sr-only">최근 AI 추천과 승인 상태</caption>
                <thead>
                  <tr>
                    <th>분석 시각 · KST</th>
                    <th>마켓</th>
                    <th>판단</th>
                    <th className="numeric">
                      Confidence{' '}
                      <InfoTip label="모델이 보고한 신뢰도입니다. 통계적으로 검증된 성공 확률이 아닙니다." />
                    </th>
                    <th className="numeric">거래 비율</th>
                    <th>승인 상태</th>
                    <th>
                      <span className="sr-only">상세</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {analyses.slice(0, 4).map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link
                          className="text-button"
                          style={{ color: 'var(--text)' }}
                          to={`/ai-analysis?analysis=${run.id}`}
                        >
                          {dateTime(run.startedAt)}
                        </Link>
                        <span className="table-subtext mono">{run.id}</span>
                      </td>
                      <td>
                        <strong>{run.recommendation.market.replace('KRW-', '')}</strong>
                        <span className="table-subtext">UPBIT · KRW</span>
                      </td>
                      <td>
                        <StatusBadge status={run.recommendation.action} />
                      </td>
                      <td className="numeric">{ratio(run.recommendation.confidence)}</td>
                      <td className="numeric">{ratio(run.recommendation.tradeRatio)}</td>
                      <td>
                        <StatusBadge status={run.recommendation.approvalStatus} />
                      </td>
                      <td>
                        <Link
                          aria-label={`${run.id} 분석 상세`}
                          className="text-button"
                          to={`/ai-analysis?analysis=${run.id}`}
                        >
                          <ArrowUpRight size={16} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="아직 분석 기록이 없습니다" />
          )}
          <div className="quick-facts">
            <div>
              <small>마지막 AI 분석</small>
              <strong>{dateTime(summary.lastAnalysisAt)}</strong>
            </div>
            <div>
              <small>처리 결과</small>
              {analyses[0] ? <StatusBadge status={analyses[0].status} /> : '—'}
            </div>
          </div>
        </Panel>
        <Panel
          title="시스템 상태"
          description="운영 연결 없이 표시하는 Demo 상태"
          action={
            <Link className="text-button" to="/operations">
              <ArrowUpRight size={15} />
              <span className="sr-only">운영 모니터링 보기</span>
            </Link>
          }
        >
          <div className="worker-list">
            {operations.workers
              .filter((worker) =>
                ['scheduler', 'outcome', 'lifecycle', 'shadow'].includes(worker.id),
              )
              .map((worker) => (
                <div className="worker-list-item" key={worker.id}>
                  <span>{worker.name}</span>
                  <StatusBadge status={worker.status} />
                </div>
              ))}
            {!operations.workers.length && <EmptyState title="표시할 서비스가 없습니다" />}
          </div>
          <div className="demo-footnote">
            <Info size={12} />
            실시간 상태가 아닌 고정 Mock 스냅샷입니다.
          </div>
        </Panel>
      </div>
      <Panel
        title="보유 자산"
        description="평가금액 상위 자산 · 현금 제외"
        action={
          <Link className="text-button" to="/portfolio">
            자산 및 거래 보기 <ArrowUpRight size={14} />
          </Link>
        }
      >
        {topPositions.length ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>자산</th>
                  <th className="numeric">보유 수량</th>
                  <th className="numeric">평가금액</th>
                  <th className="numeric">평가손익</th>
                  <th className="numeric">계좌 비중</th>
                </tr>
              </thead>
              <tbody>
                {topPositions.map((position) => (
                  <tr key={position.id}>
                    <td>
                      <CoinIdentity
                        symbol={position.asset}
                        name={position.name}
                        market={position.market}
                      />
                    </td>
                    <td className="numeric">
                      {quantity(position.quantity)}{' '}
                      <span className="text-muted small">{position.asset}</span>
                    </td>
                    <td className="numeric">{krw(position.valueKrw)}</td>
                    <td className="numeric">
                      <Change value={toNumber(position.unrealizedPnlKrw)}>
                        {krw(position.unrealizedPnlKrw, true)}
                      </Change>
                    </td>
                    <td className="numeric">{percent(position.weightPercent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="보유 자산이 없습니다" />
        )}
      </Panel>
      <div className="notice">
        <ShieldCheck />
        <span>
          조회 전용 데모입니다. BUY/SELL 추천은 실제 체결이 아니며, 실제 주문에는 Telegram 승인과
          모든 실행 안전장치가 필요합니다. HOLD는 정상적인 판단입니다.
        </span>
      </div>
    </div>
  );
}
