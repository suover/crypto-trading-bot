import { useState } from 'react';
import { Activity, Clock3, Info, Layers3, Search, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router';
import { EmptyState, MetricCard, Panel, SectionHeading, StatusBadge } from '@/components/common';
import { useDashboardData } from '@/hooks/use-dashboard-data';
import { dateTime, shortTime } from '@/lib/format';

export default function OperationsPage() {
  const { data } = useDashboardData();
  const [workerSearch, setWorkerSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [eventFilter, setEventFilter] = useState('ALL');
  const [alertFilter, setAlertFilter] = useState('ALL');
  if (!data) return null;
  const { workers, events, alerts, schedule } = data.operations;
  const lifecycle = data.research.lifecycle;
  const filteredWorkers = workers.filter(
    (worker) =>
      `${worker.name} ${worker.description} ${worker.container}`
        .toLowerCase()
        .includes(workerSearch.toLowerCase()) &&
      (statusFilter === 'ALL' || worker.status === statusFilter),
  );
  const filteredEvents = events.filter(
    (event) => eventFilter === 'ALL' || event.type === eventFilter,
  );
  const filteredAlerts = alerts.filter(
    (alert) => alertFilter === 'ALL' || alert.severity === alertFilter,
  );
  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="OPERATIONS CENTER"
        title="운영 모니터링"
        description="분석 파이프라인과 연구 워커의 흐름을 확인합니다. 모든 상태는 고정된 Demo 데이터입니다."
        action={
          <span className="chip">
            <ShieldCheck size={13} />
            조회 전용
          </span>
        }
      />
      <div className="grid-metrics">
        <MetricCard
          label="정상 실행 서비스"
          value={`${workers.filter((worker) => worker.status === 'RUNNING').length} / ${workers.length}`}
          detail="Running · Demo 관측 상태"
          icon={<Activity />}
        />
        <MetricCard
          label="확인 필요한 서비스"
          value={workers.filter((worker) => ['ERROR', 'UNKNOWN'].includes(worker.status)).length}
          detail="Error + Unknown"
          icon={<Info />}
        />
        <MetricCard
          label="최근 분석 소요"
          value={workers.length ? `${schedule.durationSeconds}초` : '—'}
          detail="시작부터 완료까지"
          icon={<Clock3 />}
        />
        <MetricCard
          label="Research Lifecycle 주기"
          value={`${lifecycle.intervalSeconds / 3600}시간`}
          detail={`최근 ${lifecycle.processedCount}개 후보 검증`}
          icon={<Layers3 />}
        />
      </div>
      <div className="notice">
        <Info />
        <span>
          아래 Running은 서버에 연결해 측정한 값이 아닙니다. 서비스 container와 기능 활성화는 서로
          다르며, Disabled는 설정상 정상 idle 상태일 수 있습니다.
        </span>
      </div>
      <Panel
        title="서비스 현황"
        description="서비스 상태 · 최근 실행 · 비밀값을 제외한 설정"
        action={<span className="chip">{filteredWorkers.length} services</span>}
      >
        <div className="toolbar">
          <label className="search-field">
            <Search size={15} />
            <input
              className="field"
              aria-label="서비스 검색"
              placeholder="서비스 검색"
              value={workerSearch}
              onChange={(event) => setWorkerSearch(event.target.value)}
            />
          </label>
          <select
            className="field"
            aria-label="서비스 상태 필터"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="ALL">모든 상태</option>
            <option value="RUNNING">Running</option>
            <option value="DISABLED">Disabled</option>
            <option value="ERROR">Error</option>
            <option value="UNKNOWN">Unknown</option>
          </select>
        </div>
        <div className="panel-body">
          {filteredWorkers.length ? (
            <div className="worker-grid">
              {filteredWorkers.map((worker) => (
                <article className="card worker-card" key={worker.id}>
                  <div className="worker-card-top">
                    <h3>{worker.name}</h3>
                    <StatusBadge status={worker.status} />
                  </div>
                  <p>{worker.description}</p>
                  <div className="worker-times">
                    <div>
                      <span>마지막 정상</span>
                      <time>{dateTime(worker.lastSuccessAt)}</time>
                    </div>
                    <div>
                      <span>마지막 오류</span>
                      <time>{dateTime(worker.lastErrorAt)}</time>
                    </div>
                  </div>
                  <details className="worker-config">
                    <summary>데모 설정 보기</summary>
                    <dl>
                      <dt>Container</dt>
                      <dd className="mono">{worker.container}</dd>
                      {Object.entries(worker.configuration).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd className="mono">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="조건에 맞는 서비스가 없습니다" />
          )}
        </div>
      </Panel>
      <div className="grid-two">
        <Panel
          title="정기 AI 분석"
          description="최근 실행 및 다음 예정 · KST"
          action={workers.length ? <StatusBadge status={schedule.status} /> : undefined}
        >
          {workers.length ? (
            <>
              <div className="schedule-grid">
                <div>
                  <span className="stat-label">최근 분석</span>
                  <Link
                    className="stat-value text-button mono"
                    to={`/ai-analysis?analysis=${schedule.lastRunId}`}
                  >
                    {schedule.lastRunId} →
                  </Link>
                </div>
                <div>
                  <span className="stat-label">다음 실행 예정</span>
                  <strong className="stat-value">{dateTime(schedule.nextRunAt)}</strong>
                </div>
                <div>
                  <span className="stat-label">시작</span>
                  <strong className="stat-value">{dateTime(schedule.startedAt)}</strong>
                </div>
                <div>
                  <span className="stat-label">완료</span>
                  <strong className="stat-value">{dateTime(schedule.finishedAt)}</strong>
                </div>
              </div>
              {schedule.error && <p className="notice">{schedule.error}</p>}
            </>
          ) : (
            <EmptyState title="분석 일정 데이터가 없습니다" />
          )}
        </Panel>
        <Panel
          title="Research Lifecycle"
          description="Phase 1 · Gate 검증 → 조건부 Shadow 등록"
          action={<StatusBadge status={lifecycle.gateEnabled ? 'RUNNING' : 'DISABLED'} />}
        >
          <div className="schedule-grid">
            <div>
              <span className="stat-label">자동 Gate 검증</span>
              <strong className="stat-value">
                {lifecycle.gateEnabled ? '활성 · Demo' : '비활성'}
              </strong>
            </div>
            <div>
              <span className="stat-label">자동 Shadow 등록</span>
              <strong className="stat-value">
                {lifecycle.autoEnrollmentEnabled ? '조건부 활성 · Demo' : '비활성'}
              </strong>
            </div>
            <div>
              <span className="stat-label">최근 검증 / 등록 후보</span>
              <strong className="stat-value">
                {lifecycle.processedCount} / {lifecycle.registeredCount}
              </strong>
            </div>
            <div>
              <span className="stat-label">기준 미충족 / 오류</span>
              <strong className="stat-value">
                {lifecycle.rejectedCount} / {lifecycle.errorCount}
              </strong>
            </div>
            <div>
              <span className="stat-label">신규 Shadow 등록</span>
              <strong className="stat-value">{lifecycle.enrolledCount}</strong>
            </div>
            <div>
              <span className="stat-label">마지막 실행</span>
              <strong className="stat-value">{dateTime(lifecycle.lastRunAt)}</strong>
            </div>
          </div>
          <p className="demo-footnote">
            <ShieldCheck size={12} />
            자동 생성·사람 승인·LIVE 활성화는 수행하지 않습니다.
          </p>
        </Panel>
      </div>
      <div className="grid-two">
        <Panel
          title="최근 활동"
          description="분석·주문·연구 이벤트 타임라인"
          action={
            <select
              className="field"
              aria-label="활동 유형 필터"
              value={eventFilter}
              onChange={(event) => setEventFilter(event.target.value)}
            >
              <option value="ALL">모든 활동</option>
              <option value="ANALYSIS">AI 분석</option>
              <option value="RECOMMENDATION">추천</option>
              <option value="ORDER">주문 상태</option>
              <option value="RESEARCH">연구</option>
              <option value="SHADOW">Shadow</option>
              <option value="SYSTEM">시스템</option>
            </select>
          }
        >
          {filteredEvents.length ? (
            <ol className="event-list">
              {filteredEvents.map((event) => (
                <li className="event-item" key={event.id}>
                  <time title={dateTime(event.occurredAt)}>
                    {shortTime(event.occurredAt)}
                    <span className="table-subtext">{dateTime(event.occurredAt).slice(5, 10)}</span>
                  </time>
                  <span
                    className="event-marker"
                    style={{
                      background:
                        event.level === 'ERROR'
                          ? 'var(--negative)'
                          : event.level === 'WARNING'
                            ? 'var(--warning)'
                            : undefined,
                    }}
                  />
                  <div>
                    <h3>{event.title}</h3>
                    <p>{event.description}</p>
                    <p className="mono">{event.service}</p>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState title="해당 유형의 활동이 없습니다" />
          )}
        </Panel>
        <Panel
          title="운영 경고"
          description="해결 상태도 Mock 기록이며 변경할 수 없습니다"
          action={
            <select
              className="field"
              aria-label="경고 심각도 필터"
              value={alertFilter}
              onChange={(event) => setAlertFilter(event.target.value)}
            >
              <option value="ALL">모든 수준</option>
              <option value="ERROR">Error</option>
              <option value="WARNING">Warning</option>
              <option value="INFO">Info</option>
            </select>
          }
        >
          <div className="panel-body stack">
            {filteredAlerts.map((alert) => (
              <article className="card worker-card" key={alert.id}>
                <div className="section-inline">
                  <StatusBadge status={alert.severity} />
                  <StatusBadge status={alert.status} />
                </div>
                <h3>{alert.title}</h3>
                <p>{alert.description}</p>
                <div className="small text-muted">
                  {dateTime(alert.occurredAt)} · {alert.service}
                </div>
              </article>
            ))}
            {!filteredAlerts.length && <EmptyState title="해당 수준의 경고가 없습니다" />}
          </div>
        </Panel>
      </div>
    </div>
  );
}
