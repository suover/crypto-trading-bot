import { useRef, useState } from 'react';
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  FlaskConical,
  Info,
  LockKeyhole,
  Search,
  ShieldCheck,
} from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  DetailDrawer,
  EmptyState,
  MetricCard,
  Panel,
  SectionHeading,
  StatusBadge,
} from '../../components/common';
import { Button } from '../../components/ui/button';
import { useDashboardData } from '../../hooks/use-dashboard-data';
import { dateOnly, dateTime, percent } from '../../lib/format';
import type {
  GateStatus,
  LifecycleStage,
  PromotionGateCheck,
  RankingWeights,
  ResearchEvidence,
  ResearchPolicyCandidate,
} from '../../types/dashboard';
import './research.css';

const weightLabels: Record<keyof RankingWeights, string> = {
  liquidity: 'Liquidity',
  trendAlignment: 'Trend',
  momentum: 'Momentum',
  volumeConfirmation: 'Volume',
  spread: 'Spread',
  volatility: 'Volatility',
  drawdown: 'Drawdown',
};

const stageLabels: Record<LifecycleStage['status'], string> = {
  COMPLETE: '완료',
  IN_PROGRESS: '검증 중',
  INSUFFICIENT_DATA: '데이터 부족',
  NOT_ELIGIBLE: '기준 미충족',
  ELIGIBLE_FOR_REVIEW: '검토 가능',
  NOT_STARTED: '미진행',
  INVALID: '무효',
};

const gateLabels: Record<GateStatus, string> = {
  NOT_ELIGIBLE: '기준 미충족',
  ELIGIBLE_FOR_REVIEW: '검토 가능',
  INSUFFICIENT_DATA: '데이터 부족',
  INVALID_PROMOTION_DATA: '데이터 무효',
};

const weightPercent = new Intl.NumberFormat('ko-KR', {
  style: 'percent',
  maximumFractionDigits: 2,
});

function lifecycleTone(status: LifecycleStage['status']) {
  if (status === 'COMPLETE') return 'complete';
  if (status === 'INVALID') return 'invalid';
  if (status === 'NOT_STARTED') return 'neutral';
  return 'attention';
}

function Lifecycle({ stages }: { stages: LifecycleStage[] }) {
  return (
    <ol className="research-lifecycle" aria-label="선택한 후보의 연구 단계">
      {stages.map((stage, index) => (
        <li key={stage.id}>
          <div className="research-stage-marker">
            <span className="research-stage-icon" data-tone={lifecycleTone(stage.status)}>
              {stage.status === 'COMPLETE' ? (
                <Check size={14} aria-hidden="true" />
              ) : (
                String(index + 1).padStart(2, '0')
              )}
            </span>
          </div>
          <strong className="research-stage-title">{stage.label}</strong>
          <span className="research-stage-state">{stageLabels[stage.status]}</span>
          <p className="research-stage-detail">{stage.detail}</p>
        </li>
      ))}
    </ol>
  );
}

function WeightComparison({
  baseline,
  candidate,
}: {
  baseline: RankingWeights;
  candidate: RankingWeights;
}) {
  const keys = Object.keys(weightLabels) as (keyof RankingWeights)[];
  const maximum = Math.max(...Object.values(baseline), ...Object.values(candidate));
  const baselineTotal = Object.values(baseline).reduce((sum, value) => sum + value, 0);
  const candidateTotal = Object.values(candidate).reduce((sum, value) => sum + value, 0);

  return (
    <div>
      <div className="research-weights" aria-label="Baseline과 후보의 가중치 비교">
        {keys.map((key) => (
          <div className="research-weight-row" key={key}>
            <span className="research-weight-label">{weightLabels[key]}</span>
            <div className="research-weight-bars" aria-hidden="true">
              <div className="research-weight-track">
                <span style={{ width: `${maximum > 0 ? (baseline[key] / maximum) * 100 : 0}%` }} />
              </div>
              <div className="research-weight-track candidate">
                <span style={{ width: `${maximum > 0 ? (candidate[key] / maximum) * 100 : 0}%` }} />
              </div>
            </div>
            <div className="research-weight-values">
              <span className="text-muted" aria-label={`Baseline ${weightLabels[key]}`}>
                {weightPercent.format(baseline[key])}
              </span>
              <ArrowRight size={11} aria-hidden="true" />
              <span
                data-changed={baseline[key] !== candidate[key]}
                aria-label={`후보 ${weightLabels[key]}`}
              >
                {weightPercent.format(candidate[key])}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="research-weight-total">
        <span>총 가중치</span>
        <span>
          Baseline {weightPercent.format(baselineTotal)} · 후보{' '}
          {weightPercent.format(candidateTotal)}
        </span>
      </div>
    </div>
  );
}

function checkValue(value: number | null, unit: PromotionGateCheck['unit']) {
  if (value === null) return '—';
  return unit === '%' ? percent(value) : `${value.toLocaleString('ko-KR')}개`;
}

function GateChecks({
  checks,
  compact = false,
}: {
  checks: PromotionGateCheck[];
  compact?: boolean;
}) {
  if (checks.length === 0) {
    return (
      <p className="research-context-note">
        <Info size={16} aria-hidden="true" />
        데이터 무결성 확인 전에는 개별 기준을 판정하지 않습니다. 제공된 검증 항목이 없습니다.
      </p>
    );
  }
  const visibleChecks = compact
    ? checks.filter((check) => check.status !== 'PASS').slice(0, 3)
    : checks;
  if (visibleChecks.length === 0) {
    return (
      <p className="research-context-note">
        <ShieldCheck size={16} aria-hidden="true" />
        표시된 검증 항목에서 기준 미충족이 없습니다. 검토 가능 상태는 미래 수익을 보장하지 않습니다.
      </p>
    );
  }
  return (
    <div className="research-check-list">
      {visibleChecks.map((check) => (
        <div className="research-check-item" key={`${check.id}-${check.window}`}>
          <div>
            <strong>
              {check.label} · {check.window}
            </strong>
            <small>
              <code>{check.id}</code>
            </small>
            <small>
              관측값 {checkValue(check.observed, check.unit)} / 기준 {check.operator}{' '}
              {checkValue(check.threshold, check.unit)}
            </small>
          </div>
          <StatusBadge status={check.status} />
        </div>
      ))}
    </div>
  );
}

function ForwardEvidence({
  candidate,
  showTable = true,
}: {
  candidate: ResearchPolicyCandidate;
  showTable?: boolean;
}) {
  const rows = candidate.forwardHorizons.map((item) => ({
    ...item,
    label: `${item.horizonMinutes / 60}시간`,
  }));
  const hasValues = rows.some(
    (item) => item.baselineMeanReturnPercent !== null || item.candidateMeanReturnPercent !== null,
  );

  return (
    <div>
      {hasValues ? (
        <div
          className="research-forward-chart"
          aria-label="1시간, 4시간, 24시간의 Baseline과 후보 평균 gross return 비교"
        >
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <BarChart data={rows} margin={{ top: 10, right: 4, bottom: 4, left: -18 }} barGap={5}>
              <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                dy={8}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                tickFormatter={(value: number) => `${value}%`}
              />
              <Tooltip
                cursor={{ fill: 'var(--surface-elevated)' }}
                contentStyle={{
                  background: 'var(--surface-elevated)',
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  color: 'var(--text)',
                  fontSize: 12,
                }}
                formatter={(value) => percent(typeof value === 'number' ? value : null)}
              />
              <Bar
                name="Baseline gross"
                dataKey="baselineMeanReturnPercent"
                fill="var(--text-muted)"
                radius={[3, 3, 0, 0]}
                maxBarSize={30}
                isAnimationActive={false}
              />
              <Bar
                name="후보 gross"
                dataKey="candidateMeanReturnPercent"
                fill="var(--primary)"
                radius={[3, 3, 0, 0]}
                maxBarSize={30}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <EmptyState
          title="Forward 성과를 계산할 수 없습니다"
          description="표본이 부족하거나 데이터 무결성 검증을 통과하지 못했습니다. 결측값은 0으로 대체하지 않습니다."
        />
      )}
      {showTable && (
        <div className="table-scroll">
          <table className="data-table research-evidence-table">
            <caption className="sr-only">
              기간별 Forward 연구 성과. 실제 체결 손익이 아닌 Mock 연구 데이터입니다.
            </caption>
            <thead>
              <tr>
                <th scope="col">구간</th>
                <th scope="col">Baseline</th>
                <th scope="col">후보</th>
                <th scope="col">Gross Δ</th>
                <th scope="col">비용 반영 Δ</th>
                <th scope="col">비용 반영 Win rate</th>
                <th scope="col">표본</th>
                <th scope="col">Gate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.horizonMinutes}>
                  <th scope="row">{row.label}</th>
                  <td>{percent(row.baselineMeanReturnPercent)}</td>
                  <td>{percent(row.candidateMeanReturnPercent)}</td>
                  <td
                    className={
                      row.meanDeltaPercent !== null && row.meanDeltaPercent >= 0 ? 'positive' : ''
                    }
                  >
                    {percent(row.meanDeltaPercent, true)}
                  </td>
                  <td
                    className={
                      row.costAdjustedDeltaPercent !== null && row.costAdjustedDeltaPercent < 0
                        ? 'negative'
                        : ''
                    }
                  >
                    {percent(row.costAdjustedDeltaPercent, true)}
                  </td>
                  <td>{percent(row.winRatePercent)}</td>
                  <td>{row.sampleCount}</td>
                  <td>
                    <StatusBadge status={row.gateStatus} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EvidenceSummary({ evidence, title }: { evidence: ResearchEvidence; title: string }) {
  return (
    <div className="research-drawer-section">
      <div className="research-candidate-title">
        <h3>{title}</h3>
        <StatusBadge status={evidence.status} />
      </div>
      <dl className="research-provenance">
        <div>
          <dt>검증 구간 · KST</dt>
          <dd>
            {dateOnly(evidence.from)} → {dateOnly(evidence.to)}
          </dd>
        </div>
        <div>
          <dt>동일 비교 표본</dt>
          <dd>{evidence.sampleCount.toLocaleString('ko-KR')}개</dd>
        </div>
        <div>
          <dt>Baseline 평균 gross return</dt>
          <dd>{percent(evidence.baselineReturnPercent)}</dd>
        </div>
        <div>
          <dt>후보 평균 gross return</dt>
          <dd>{percent(evidence.grossReturnPercent)}</dd>
        </div>
        <div>
          <dt>후보 평균 cost-adjusted return</dt>
          <dd>{percent(evidence.costAdjustedReturnPercent)}</dd>
        </div>
        <div>
          <dt>비용 반영 Win rate</dt>
          <dd>{percent(evidence.winRatePercent)}</dd>
        </div>
        <div>
          <dt>평균 replacement rate</dt>
          <dd>{percent(evidence.turnoverPercent)}</dd>
        </div>
      </dl>
    </div>
  );
}

function CandidateDetail({
  candidate,
  baseline,
}: {
  candidate: ResearchPolicyCandidate;
  baseline: RankingWeights;
}) {
  const tabs = ['historical', 'forward', 'shadow'] as const;
  type EvidenceTab = (typeof tabs)[number];
  const [tab, setTab] = useState<EvidenceTab>('forward');
  const tabRefs = useRef<Partial<Record<EvidenceTab, HTMLButtonElement | null>>>({});
  return (
    <div className="research-drawer-section">
      <div className="research-gate-summary">
        <StatusBadge status={candidate.gate.status} />
        <p>{candidate.gate.reason}</p>
      </div>
      <dl className="research-provenance">
        <div>
          <dt>Candidate ID</dt>
          <dd className="mono">{candidate.id}</dd>
        </div>
        <div>
          <dt>Reference Snapshot</dt>
          <dd className="mono">{candidate.referenceSnapshotId}</dd>
        </div>
        <div>
          <dt>등록 시점 · KST</dt>
          <dd>{dateTime(candidate.registeredAt)}</dd>
        </div>
        <div>
          <dt>기준 정책 / Effective Top N</dt>
          <dd>
            {candidate.baseline} / {candidate.effectiveTopN}
          </dd>
        </div>
      </dl>
      <h3>Ranking 가중치</h3>
      <WeightComparison baseline={baseline} candidate={candidate.weights} />
      <div className="tabs" role="tablist" aria-label="후보 검증 영역">
        {tabs.map((item, index) => (
          <button
            key={item}
            type="button"
            role="tab"
            className={`tab ${tab === item ? 'active' : ''}`}
            aria-selected={tab === item}
            aria-controls={`research-${item}-panel`}
            id={`research-${item}-tab`}
            tabIndex={tab === item ? 0 : -1}
            ref={(element) => {
              tabRefs.current[item] = element;
            }}
            onClick={() => setTab(item)}
            onKeyDown={(event) => {
              const nextIndex =
                event.key === 'ArrowRight'
                  ? (index + 1) % tabs.length
                  : event.key === 'ArrowLeft'
                    ? (index + tabs.length - 1) % tabs.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? tabs.length - 1
                        : null;
              if (nextIndex === null) return;
              event.preventDefault();
              const nextTab = tabs[nextIndex];
              if (nextTab) {
                setTab(nextTab);
                tabRefs.current[nextTab]?.focus();
              }
            }}
          >
            {item === 'historical' ? 'Historical' : item === 'forward' ? 'Forward' : 'Shadow'}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`research-${tab}-panel`}
        aria-labelledby={`research-${tab}-tab`}
        className="research-drawer-section"
      >
        {tab === 'historical' && (
          <>
            <EvidenceSummary
              evidence={candidate.historical}
              title="등록 시점으로 제한한 Historical"
            />
            <p className="research-context-note">
              <Info size={15} aria-hidden="true" />
              후보 설계에 사용되었을 수 있는 과거 데이터입니다. 독립적인 unseen 성과로 해석하지
              않습니다.
            </p>
          </>
        )}
        {tab === 'forward' && (
          <>
            <EvidenceSummary evidence={candidate.forward} title="등록 이후 Forward evidence" />
            <ForwardEvidence candidate={candidate} />
            <h3>Promotion Gate 검증 항목</h3>
            <GateChecks checks={candidate.gate.checks} />
          </>
        )}
        {tab === 'shadow' &&
          (candidate.shadowEnrollment ? (
            <>
              <dl className="research-provenance">
                <div>
                  <dt>Shadow Enrollment</dt>
                  <dd className="mono">{candidate.shadowEnrollment.id}</dd>
                </div>
                <div>
                  <dt>등록 시점 · KST</dt>
                  <dd>{dateTime(candidate.shadowEnrollment.enrolledAt)}</dd>
                </div>
                <div>
                  <dt>Snapshot watermark</dt>
                  <dd>{candidate.shadowEnrollment.snapshotWatermark}</dd>
                </div>
                <div>
                  <dt>Selection 표본</dt>
                  <dd>{candidate.shadowEvaluation?.sampleCount ?? '—'}</dd>
                </div>
                <div>
                  <dt>Shadow gross return</dt>
                  <dd>{percent(candidate.shadowEvaluation?.grossReturnPercent ?? null)}</dd>
                </div>
                <div>
                  <dt>Shadow Review</dt>
                  <dd>
                    <StatusBadge status={candidate.shadowEvaluation?.reviewStatus ?? 'UNKNOWN'} />
                  </dd>
                </div>
              </dl>
              <p className="research-context-note">
                <Info size={15} aria-hidden="true" />
                Shadow는 등록 이후 frozen snapshot의 선택을 비교하며 실제 주문을 실행하지 않습니다.
                기존 enrollment는 immutable 기록입니다.
              </p>
            </>
          ) : (
            <EmptyState
              title="Shadow에 등록되지 않은 후보입니다"
              description="신규 등록은 현재 Promotion Gate 자격 검증을 통과해야 합니다. 이 화면에서는 등록 작업을 실행할 수 없습니다."
            />
          ))}
      </div>
      <h3>사람 승인과 Runtime 활성화</h3>
      <dl className="research-provenance">
        <div>
          <dt>Human Policy Approval</dt>
          <dd>{candidate.humanApproval === 'APPROVED' ? '승인 기록 있음' : '승인 기록 없음'}</dd>
        </div>
        <div>
          <dt>Full LIVE Activation</dt>
          <dd>
            {candidate.activationStatus === 'FULL_LIVE' ? '활성화 기록 있음' : '활성화 기록 없음'}
          </dd>
        </div>
      </dl>
      <p className="research-context-note">
        <LockKeyhole size={15} aria-hidden="true" />
        사람의 승인과 활성화는 별도 단계입니다. Full LIVE Activation 자체도 주문을 생성하지
        않습니다. 이 대시보드는 조회 전용입니다.
      </p>
    </div>
  );
}

export default function ResearchPage() {
  const { data } = useDashboardData();
  const [search, setSearch] = useState('');
  const [gateFilter, setGateFilter] = useState('ALL');
  const [sort, setSort] = useState('newest');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  if (!data) return null;

  const { candidates, baselineWeights, runtimePolicy, lifecycle } = data.research;
  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? candidates[0];
  const filtered = candidates
    .filter((candidate) => {
      const matchesSearch = `${candidate.id} ${candidate.name}`
        .toLowerCase()
        .includes(search.toLowerCase().trim());
      return matchesSearch && (gateFilter === 'ALL' || candidate.gate.status === gateFilter);
    })
    .sort((a, b) =>
      sort === 'newest'
        ? b.registeredAt.localeCompare(a.registeredAt)
        : a.registeredAt.localeCompare(b.registeredAt),
    );
  const completed = candidates.filter(
    (candidate) =>
      candidate.gate.status === 'NOT_ELIGIBLE' || candidate.gate.status === 'ELIGIBLE_FOR_REVIEW',
  ).length;
  const enrolled = candidates.filter((candidate) => candidate.shadowEnrollment !== null).length;

  function showCandidate(candidate: ResearchPolicyCandidate) {
    setSelectedId(candidate.id);
    setDrawerOpen(true);
  }

  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="RESEARCH WORKSPACE"
        title="증거를 쌓고, 정책을 검증합니다."
        description="Historical · Forward · Shadow evidence를 분리한 Ranking 연구 공간입니다."
        action={
          <span className="research-readonly">
            <LockKeyhole size={13} />
            조회 전용
          </span>
        }
      />
      <section className="research-runtime" aria-label="현재 Runtime Ranking 정책">
        <div className="research-runtime-heading">
          <FlaskConical size={27} aria-hidden="true" />
          <div>
            <p>현재 Runtime Ranking · Demo</p>
            <h2>{runtimePolicy.name}</h2>
            <p>연구 후보는 검증만으로 현재 정책을 대체하지 않습니다.</p>
          </div>
        </div>
        <div className="research-runtime-meta">
          <StatusBadge status={runtimePolicy.mode} />
          <span className="text-muted text-xs">기준 적용 {dateOnly(runtimePolicy.since)}</span>
        </div>
      </section>
      <div className="grid-metrics">
        <MetricCard
          label="연구 스냅샷"
          value={data.summary.researchSnapshotCount.toLocaleString('ko-KR')}
          detail={`최근 수집 ${dateTime(data.summary.latestResearchAt)}`}
        />
        <MetricCard
          label="등록 정책 후보"
          value={candidates.length.toString()}
          detail="immutable candidate registry"
        />
        <MetricCard
          label="Gate 검증 완료"
          value={`${completed} / ${candidates.length}`}
          detail="표본 충족 · 통과 또는 미충족 판정"
        />
        <MetricCard
          label="Shadow 등록"
          value={enrolled.toString()}
          detail="실거래 없는 독립 검증"
        />
      </div>

      {selected && (
        <Panel
          title="Research lifecycle"
          description="선택한 후보의 기록으로 확인되는 단계만 표시합니다."
          action={
            <label className="research-scenario-select">
              <span className="sr-only">비교할 연구 후보</span>
              <select
                className="field"
                value={selected.id}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {candidates.map((candidate) => (
                  <option value={candidate.id} key={candidate.id}>
                    {candidate.name}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          <div className="panel-body">
            <Lifecycle stages={selected.lifecycle} />
            <p className="research-context-note" style={{ marginTop: 20 }}>
              <Info size={15} aria-hidden="true" />
              Phase 1: 등록된 후보의 Gate 검증과 조건부 Shadow 등록까지 자동화합니다. 새 후보 자동
              생성과 Telegram 전략 승인은 후속 단계입니다.
            </p>
          </div>
        </Panel>
      )}

      <Panel
        title="Ranking 정책 후보"
        description="후보 등록과 Runtime 활성화는 별개입니다. 모든 수치와 상태는 Mock 데이터입니다."
        action={<span className="text-muted text-xs">{filtered.length}개 후보</span>}
      >
        <div className="toolbar">
          <label className="search-field">
            <Search size={15} aria-hidden="true" />
            <span className="sr-only">연구 후보 검색</span>
            <input
              className="field"
              placeholder="정책명 또는 Candidate ID 검색"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label>
            <span className="sr-only">Promotion Gate 상태 필터</span>
            <select
              className="field"
              value={gateFilter}
              onChange={(event) => setGateFilter(event.target.value)}
            >
              <option value="ALL">모든 Gate 상태</option>
              {Object.entries(gateLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">연구 후보 정렬</span>
            <select
              className="field"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              <option value="newest">최근 등록순</option>
              <option value="oldest">오래된 등록순</option>
            </select>
          </label>
        </div>
        {filtered.length === 0 ? (
          <EmptyState
            title="조건에 맞는 연구 후보가 없습니다"
            description="정책명 또는 Gate 상태 필터를 변경해 보세요."
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <caption className="sr-only">등록된 Ranking 정책과 Gate, Shadow, 활성화 상태</caption>
              <thead>
                <tr>
                  <th scope="col">정책 / Candidate ID</th>
                  <th scope="col">등록 시각 · KST</th>
                  <th scope="col">기준 Ranking</th>
                  <th scope="col">Forward</th>
                  <th scope="col">Promotion Gate</th>
                  <th scope="col">Shadow</th>
                  <th scope="col">활성화</th>
                  <th scope="col">
                    <span className="sr-only">상세</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((candidate) => (
                  <tr
                    key={candidate.id}
                    className={candidate.id === selected?.id ? 'research-selected-row' : ''}
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => showCandidate(candidate)}
                      >
                        {candidate.name}
                      </button>
                      <div className="text-muted mono text-xs">{candidate.id}</div>
                    </th>
                    <td className="text-muted">{dateTime(candidate.registeredAt)}</td>
                    <td>{candidate.baseline}</td>
                    <td>
                      <StatusBadge status={candidate.forward.status} />
                    </td>
                    <td>
                      <StatusBadge status={candidate.gate.status} />
                    </td>
                    <td>
                      {candidate.shadowEnrollment ? (
                        <StatusBadge status="ENROLLED" />
                      ) : (
                        <span className="text-muted">미등록</span>
                      )}
                    </td>
                    <td>
                      {candidate.activationStatus === 'FULL_LIVE' ? (
                        <StatusBadge status="FULL_LIVE" />
                      ) : (
                        <span className="text-muted">비활성</span>
                      )}
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`${candidate.name} 상세보기`}
                        onClick={() => showCandidate(candidate)}
                      >
                        <ChevronRight size={16} />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {selected && (
        <>
          <div className="grid-two">
            <Panel
              title="Ranking 가중치 비교"
              description={`${selected.name} · BASELINE 대비`}
              action={
                <div className="research-legend">
                  <span>
                    <i className="research-swatch" />
                    Baseline
                  </span>
                  <span>
                    <i className="research-swatch research-swatch-candidate" />
                    후보
                  </span>
                </div>
              }
            >
              <div className="panel-body">
                <WeightComparison baseline={baselineWeights} candidate={selected.weights} />
              </div>
            </Panel>
            <Panel
              title="Promotion Gate"
              description={`최근 검증 ${dateTime(selected.gate.evaluatedAt)}`}
              action={
                <Button variant="ghost" onClick={() => setDrawerOpen(true)}>
                  검증 상세 <ChevronRight size={14} />
                </Button>
              }
            >
              <div className="panel-body">
                <div className="research-gate-summary">
                  <StatusBadge status={selected.gate.status} />
                  <p>{selected.gate.reason}</p>
                </div>
                <GateChecks checks={selected.gate.checks} compact />
              </div>
            </Panel>
          </div>
          <Panel
            title="Forward validation"
            description={`${selected.name} · 등록 이후 동일 표본의 평균 시장 움직임`}
            action={
              <div className="research-legend">
                <span>
                  <i className="research-swatch" />
                  Baseline gross
                </span>
                <span>
                  <i className="research-swatch research-swatch-candidate" />
                  후보 gross
                </span>
              </div>
            }
          >
            <div className="panel-body">
              <ForwardEvidence candidate={selected} />
              <p className="research-context-note" style={{ marginTop: 16 }}>
                <CircleHelp size={15} aria-hidden="true" />
                Gross return과 비용 반영 연구 성과는 실제 체결 손익이 아닙니다. 비용률과 Gate 기준은
                Mock 정책 정의에서 제공되며, 브라우저에서 정책을 결정하지 않습니다.
              </p>
            </div>
          </Panel>
        </>
      )}

      <div className="notice">
        <ShieldCheck size={17} aria-hidden="true" />
        <div>
          <strong>자동화의 경계는 Shadow Enrollment까지</strong>
          <p>
            자동 Gate 검증 {lifecycle.gateEnabled ? '활성' : '비활성'} · 자동 Shadow 등록{' '}
            {lifecycle.autoEnrollmentEnabled ? '활성' : '비활성'} ·{' '}
            {lifecycle.intervalSeconds / 3600}시간 주기 (Demo). Shadow Review, 사람의 승인, Full
            LIVE Activation은 각각 별도 단계이며 이 화면에서 실행할 수 없습니다.
          </p>
        </div>
      </div>

      <DetailDrawer
        open={drawerOpen && !!selected}
        onOpenChange={setDrawerOpen}
        title={selected?.name ?? '정책 상세'}
        description="후보 provenance와 검증 근거 · Mock 조회 전용"
      >
        {selected && (
          <CandidateDetail key={selected.id} candidate={selected} baseline={baselineWeights} />
        )}
      </DetailDrawer>
    </div>
  );
}
