import { useRef, useState, type ReactNode } from 'react';
import { useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, MetricCard, Panel, SectionHeading } from '@/components/common';
import {
  researchApiKey,
  useResearchSummary,
  useResearchCandidates,
  useResearchCandidateDetail,
  useResearchSnapshots,
  useResearchAutomationStatus,
} from '@/hooks/use-research-api';
import {
  authenticationPath,
  ResearchApiError,
  candidateQuery,
} from '@/services/research-api-client';
import {
  defaultFilters,
  type CandidateFilters,
  type CandidateSort,
  type LongString,
  type Page,
} from '@/types/research-api';
import { dateTime } from '@/lib/format';

const count = (value: LongString) => BigInt(value).toLocaleString('ko-KR');
function Time({ value }: { value: string | null }) {
  return value === null ? (
    <>미기록</>
  ) : (
    <time dateTime={value} title={value}>
      {dateTime(value)} KST
    </time>
  );
}
function ApiError({ error, retry }: { error: Error; retry: () => void }) {
  const status = error instanceof ResearchApiError ? error.status : undefined;
  const kind = error instanceof ResearchApiError ? error.kind : 'data';
  const message =
    status === 401
      ? '관리자 인증이 필요합니다'
      : status === 403
        ? '접근이 거부되었습니다. 관리자 권한 또는 설정된 사용자 범위를 확인하세요.'
        : status === 404
          ? '후보를 찾을 수 없습니다'
          : status === 400 || kind === 'input'
            ? '입력 조건을 확인해 주세요'
            : status !== undefined
              ? '서버 오류로 데이터를 조회하지 못했습니다'
              : kind === 'network'
                ? 'API 연결에 실패했습니다'
                : '잘못된 API 데이터입니다. 응답 계약을 확인해 주세요.';
  return (
    <div className="empty-state" role="alert">
      <h3>{message}</h3>
      <p>실패한 조회를 Mock 데이터나 0으로 대체하지 않습니다.</p>
      {status === 401 && (
        <p>
          <a
            className="text-button"
            href={authenticationPath}
            target="_blank"
            rel="noopener noreferrer"
          >
            새 탭에서 관리자 인증
          </a>{' '}
          후 이 화면에서 다시 시도하세요. 비밀번호는 브라우저 인증 창에서만 입력합니다.
        </p>
      )}
      <Button variant="outline" onClick={retry}>
        다시 시도
      </Button>
    </div>
  );
}
function QueryContent<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (value: T) => ReactNode;
}) {
  if (query.isFetching || query.isPending)
    return (
      <div role="status" className="empty-state">
        API 데이터 조회 중…
      </div>
    );
  if (query.isError)
    return (
      <ApiError
        error={query.error}
        retry={() => {
          void query.refetch();
        }}
      />
    );
  return <>{children(query.data)}</>;
}
function Pages({
  data,
  label,
  change,
}: {
  data: Pick<Page<unknown>, 'page' | 'totalPages' | 'totalElements'>;
  label: string;
  change: (page: number) => void;
}) {
  return (
    <nav className="pagination" aria-label={label + ' 페이지 이동'}>
      <span>
        총 {count(data.totalElements)}건 · {data.totalPages === '0' ? '0' : data.page + 1} /{' '}
        {count(data.totalPages)} 페이지
      </span>
      <div className="research-source-switch">
        <Button
          variant="outline"
          disabled={data.page === 0}
          aria-label={'이전 ' + label + ' 페이지'}
          onClick={() => change(data.page - 1)}
        >
          이전
        </Button>
        <Button
          variant="outline"
          disabled={BigInt(data.page) + 1n >= BigInt(data.totalPages) || data.page >= 2147483647}
          aria-label={'다음 ' + label + ' 페이지'}
          onClick={() => change(data.page + 1)}
        >
          다음
        </Button>
      </div>
    </nav>
  );
}
function Provenance({ entries }: { entries: [string, ReactNode][] }) {
  return (
    <dl className="research-provenance api-provenance">
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value ?? '미기록'}</dd>
        </div>
      ))}
    </dl>
  );
}
function Detail({ id }: { id: LongString }) {
  const query = useResearchCandidateDetail(id);
  return (
    <QueryContent query={query}>
      {(data) => (
        <div className="page-stack">
          <h3>{data.policyName}</h3>
          <Provenance
            entries={[
              ['Candidate ID', data.id],
              ['등록 시각', <Time value={data.registeredAt} />],
              ['거래소 / 기준통화', data.exchange + ' / ' + data.quoteAsset],
              ['Effective Top N', data.effectiveTopN],
              ['Reference Snapshot', data.referenceSnapshotId],
              ['Dataset Schema', data.registrationContext.datasetSchemaVersion],
              [
                'Baseline Policy Signature',
                <code>{data.registrationContext.baselinePolicySignature}</code>,
              ],
              ['Snapshot Watermark', data.registrationContext.snapshotIdWatermark],
              ['Captured Watermark', <Time value={data.registrationContext.capturedAtWatermark} />],
              [
                'Reference 관측 시각',
                <Time value={data.registrationContext.referenceSnapshotCapturedAt} />,
              ],
            ]}
          />
          <h3>Component Weights · 원본 소수</h3>
          <Provenance
            entries={Object.entries(data.componentWeights).map(([key, value]) => [
              key,
              <code>{value}</code>,
            ])}
          />
          <h3>Shadow 등록 당시 기록</h3>
          {data.enrollment ? (
            <Provenance
              entries={[
                ['Enrollment ID', data.enrollment.id],
                ['Shadow 등록 시각', <Time value={data.enrollment.shadowEnrolledAt} />],
                ['저장된 당시 Gate Status', data.enrollment.storedGateStatus],
                ['당시 Gate 평가 시각', <Time value={data.enrollment.storedGateEvaluatedAt} />],
              ]}
            />
          ) : (
            <p>Shadow 미등록</p>
          )}
          <p className="research-context-note">
            과거 등록 기록이며 현재 Gate 판정, 사람 승인 또는 Full LIVE 활성화 상태를 의미하지
            않습니다.
          </p>
          <p className="text-muted small">
            응답 생성 시각 <Time value={data.generatedAt} />
          </p>
        </div>
      )}
    </QueryContent>
  );
}
function Candidates() {
  const [filters, setFilters] = useState<CandidateFilters>(defaultFilters);
  const [draft, setDraft] = useState({ keyword: '', from: '', to: '' });
  const [formError, setFormError] = useState('');
  const [selected, setSelected] = useState<LongString | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const query = useResearchCandidates(filters);
  function update(patch: Partial<CandidateFilters>) {
    setFilters((previous) => ({ ...previous, ...patch, page: 0 }));
  }
  function search(event: React.FormEvent) {
    event.preventDefault();
    try {
      const next = {
        ...filters,
        page: 0,
        keyword: draft.keyword,
        registeredFrom: draft.from ? new Date(draft.from + '+09:00').toISOString() : '',
        registeredTo: draft.to ? new Date(draft.to + '+09:00').toISOString() : '',
      };
      candidateQuery(next);
      setFilters(next);
      setFormError('');
    } catch {
      setFormError('등록 기간의 순서와 검색 조건을 확인하세요.');
    }
  }
  return (
    <Panel
      title="Research Candidate Registry"
      description="정책명 검색 · 서버 정렬 및 페이지네이션"
    >
      <form className="api-filters panel-body" onSubmit={search}>
        <label>
          정책명 검색
          <input
            className="field"
            maxLength={80}
            placeholder="정책명에 포함된 단어"
            value={draft.keyword}
            onChange={(e) => setDraft({ ...draft, keyword: e.target.value })}
          />
        </label>
        <label>
          등록 시작 · KST
          <input
            className="field"
            type="datetime-local"
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          />
        </label>
        <label>
          등록 종료 · KST
          <input
            className="field"
            type="datetime-local"
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          />
        </label>
        <Button type="submit" variant="outline">
          검색 적용
        </Button>
        <label>
          Shadow 등록 여부
          <select
            className="field"
            value={filters.shadowEnrolled}
            onChange={(e) =>
              update({ shadowEnrolled: e.target.value as CandidateFilters['shadowEnrolled'] })
            }
          >
            <option value="">전체</option>
            <option value="true">등록됨</option>
            <option value="false">미등록</option>
          </select>
        </label>
        <label>
          후보 정렬
          <select
            className="field"
            value={filters.sort}
            onChange={(e) => update({ sort: e.target.value as CandidateSort })}
          >
            <option value="registeredAt,desc">등록 최신순</option>
            <option value="registeredAt,asc">등록 오래된순</option>
            <option value="policyName,asc">정책명 오름차순</option>
            <option value="policyName,desc">정책명 내림차순</option>
            <option value="id,asc">ID 오름차순</option>
            <option value="id,desc">ID 내림차순</option>
          </select>
        </label>
        <label>
          후보 페이지 크기
          <select
            className="field"
            value={filters.size}
            onChange={(e) => update({ size: Number(e.target.value) })}
          >
            {[10, 20, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}개
              </option>
            ))}
          </select>
        </label>
        {formError && <p role="alert">{formError}</p>}
      </form>
      <QueryContent query={query}>
        {(data) => (
          <>
            {data.items.length === 0 ? (
              <EmptyState title="조회된 후보가 없습니다" />
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <caption className="sr-only">API 연구 후보 목록</caption>
                  <thead>
                    <tr>
                      {[
                        '정책명',
                        'Candidate ID',
                        '등록 시각 · KST',
                        '거래소',
                        '기준통화',
                        'Top N',
                        'Shadow',
                        '상세',
                      ].map((h) => (
                        <th key={h} scope="col">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((c) => (
                      <tr key={c.id}>
                        <th scope="row">{c.policyName}</th>
                        <td>{c.id}</td>
                        <td>
                          <Time value={c.registeredAt} />
                        </td>
                        <td>{c.exchange}</td>
                        <td>{c.quoteAsset}</td>
                        <td>{c.effectiveTopN}</td>
                        <td>{c.shadowEnrolled ? '등록됨' : '미등록'}</td>
                        <td>
                          <Button
                            variant="ghost"
                            aria-label={c.policyName + ' API 상세보기'}
                            onClick={(e) => {
                              opener.current = e.currentTarget;
                              setSelected(c.id);
                            }}
                          >
                            상세보기
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Pages data={data} label="후보" change={(page) => setFilters({ ...filters, page })} />
            <p className="panel-body text-muted small">
              응답 생성 시각 <Time value={data.generatedAt} />
            </p>
          </>
        )}
      </QueryContent>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <DialogContent
          className="detail-drawer"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            (opener.current?.isConnected
              ? opener.current
              : document.querySelector<HTMLElement>('#main-content')
            )?.focus();
          }}
        >
          <DialogHeader>
            <span className="eyebrow">LOCAL API · READ ONLY</span>
            <DialogTitle>연구 후보 상세</DialogTitle>
            <DialogDescription>저장된 등록 정보와 과거 Gate 기록을 조회합니다.</DialogDescription>
          </DialogHeader>
          <div className="drawer-body">
            {selected !== null && <Detail key={selected} id={selected} />}
          </div>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}
function Snapshots() {
  const [page, setPage] = useState(0),
    [size, setSize] = useState(20);
  const query = useResearchSnapshots(page, size);
  return (
    <Panel
      title="Replay Snapshot History"
      description="관측 시각 내림차순 · 동일 시각은 ID 내림차순"
      action={
        <label>
          Snapshot 페이지 크기
          <select
            className="field"
            value={size}
            onChange={(e) => {
              setSize(Number(e.target.value));
              setPage(0);
            }}
          >
            {[10, 20, 50, 100].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      }
    >
      <QueryContent query={query}>
        {(data) => (
          <>
            {data.items.length === 0 ? (
              <EmptyState title="저장된 Snapshot이 없습니다" />
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <caption className="sr-only">API Snapshot 목록</caption>
                  <thead>
                    <tr>
                      {[
                        'ID',
                        '관측 시각 · KST',
                        '저장 시각 · KST',
                        'Policy Signature',
                        '전체 후보',
                        'Prefilter',
                        'Ranked',
                        'Final',
                      ].map((h) => (
                        <th key={h} scope="col">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((s) => (
                      <tr key={s.id}>
                        <th scope="row">{s.id}</th>
                        <td>
                          <Time value={s.capturedAt} />
                        </td>
                        <td>
                          <Time value={s.createdAt} />
                        </td>
                        <td className="api-signature">
                          <code>{s.policySignature}</code>
                        </td>
                        <td>{s.researchCandidateCount}</td>
                        <td>{s.prefilterCandidateCount}</td>
                        <td>{s.rankedCandidateCount}</td>
                        <td>{s.finalCandidateCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Pages data={data} label="Snapshot" change={setPage} />
            <p className="panel-body text-muted small">
              응답 생성 시각 <Time value={data.generatedAt} />
            </p>
          </>
        )}
      </QueryContent>
    </Panel>
  );
}
function Automation() {
  const query = useResearchAutomationStatus();
  return (
    <Panel
      title="Research Automation Checkpoints"
      description="저장된 Context별 상태입니다. Worker 실행 상태나 신규 적격 Snapshot 수를 추정하지 않습니다."
    >
      <QueryContent query={query}>
        {(data) => (
          <div className="panel-body page-stack">
            {!data.observed ? (
              <EmptyState
                title="미관찰 · 저장된 체크포인트가 없습니다"
                description="레코드 없음은 Worker 장애 판정이 아닙니다."
              />
            ) : (
              data.states.map((s) => (
                <article
                  className="api-state"
                  key={JSON.stringify([
                    s.scope.userId,
                    s.scope.exchange,
                    s.scope.quoteAsset,
                    s.datasetSchemaVersion,
                    s.baselinePolicySignature,
                    s.effectiveTopN,
                  ])}
                >
                  <h3>
                    {s.datasetSchemaVersion} · Top N {s.effectiveTopN}
                  </h3>
                  <Provenance
                    entries={[
                      [
                        '사용자 / 거래소 / 기준통화',
                        s.scope.userId + ' / ' + s.scope.exchange + ' / ' + s.scope.quoteAsset,
                      ],
                      ['Baseline Signature', <code>{s.baselinePolicySignature}</code>],
                      ['Last Observed Snapshot', s.lastObservedSnapshotId],
                      ['Reviewed Snapshot', s.reviewedSnapshotId],
                      ['Review Status', s.reviewStatus],
                      ['Last Status', s.lastStatus],
                      ['Last Attempt', <Time value={s.lastAttemptAt} />],
                      ['Last Reference Snapshot', s.lastReferenceSnapshotId],
                      ['Last Registration Status', s.lastRegistrationStatus],
                      ['Last Registered Snapshot', s.lastRegisteredSnapshotId],
                      ['Last Registered At', <Time value={s.lastRegisteredAt} />],
                    ]}
                  />
                </article>
              ))
            )}
            <p className="text-muted small">
              응답 생성 시각 <Time value={data.generatedAt} />
            </p>
          </div>
        )}
      </QueryContent>
    </Panel>
  );
}
export default function ResearchApiView() {
  const summary = useResearchSummary(),
    client = useQueryClient();
  return (
    <div className="page-stack research-api-view">
      <SectionHeading
        eyebrow="LOCAL RESEARCH · READ ONLY"
        title="저장된 연구 데이터를 조회합니다."
        description="로컬 Spring Boot Research API · 주문·승인·정책 변경 없음"
        action={
          <Button
            variant="outline"
            onClick={() => {
              void client.refetchQueries({ queryKey: researchApiKey, type: 'active' });
            }}
          >
            API 새로고침
          </Button>
        }
      />
      <p className="research-context-note">
        현재 Gate 판정, Historical/Forward 성과, 사람 승인, Full LIVE 활성화, Runtime 정책 및 Worker
        실행 상태는 현재 API 미제공입니다.
      </p>
      <QueryContent query={summary}>
        {(data) => (
          <>
            <div className="grid-metrics">
              <MetricCard
                label="등록 후보"
                value={count(data.candidateCount)}
                detail="Research Registry"
              />
              <MetricCard
                label="Shadow 등록"
                value={count(data.shadowEnrollmentCount)}
                detail="저장된 enrollment"
              />
              <MetricCard
                label="Replay Snapshot"
                value={count(data.snapshotCount)}
                detail="저장된 snapshot"
              />
              <MetricCard
                label="최근 Snapshot ID"
                value={data.latestSnapshotId ?? '미기록'}
                detail={<Time value={data.latestSnapshotCapturedAt} />}
              />
            </div>
            <div className="research-context-note">
              범위: 사용자 {data.scope.userId} · {data.scope.exchange} / {data.scope.quoteAsset}
              <br />
              최근 데이터 관측 시각: <Time value={data.latestSnapshotCapturedAt} />
              <br />
              응답 생성 시각: <Time value={data.generatedAt} />
            </div>
          </>
        )}
      </QueryContent>
      <Candidates />
      <Snapshots />
      <Automation />
    </div>
  );
}
