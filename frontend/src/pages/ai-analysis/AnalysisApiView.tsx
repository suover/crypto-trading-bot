import { useState } from 'react';
import { useSearchParams, useLocation } from 'react-router';
import { Button } from '@/components/ui/button';
import { DetailDrawer, EmptyState, Panel, SectionHeading } from '@/components/common';
import { useAnalysisRecommendations } from '@/hooks/use-analysis-api';
import { Source, Time, Pages } from '@/pages/overview/api-components';
import { ratioText, kstInstant } from '@/lib/overview-format';
import type { Search, DetailReference } from '@/types/analysis-api';
import { AnalysisQuery } from './api-components';
import AnalysisApiDetail from './AnalysisApiDetail';
const initial: Search = { from: '', to: '', market: '', action: '', page: 0, size: 20 };
export default function AnalysisApiView() {
  const [filter, setFilter] = useState<Search>(initial);
  const [draft, setDraft] = useState({
    from: '',
    to: '',
    market: '',
    action: '' as Search['action'],
  });
  const [inputError, setInputError] = useState(false);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const linked = (location.state as { analysisReference?: DetailReference } | null)
    ?.analysisReference;
  const [selected, setSelected] = useState<DetailReference | null>(null);
  const query = useAnalysisRecommendations(filter);
  const id = params.get('recommendation');
  const reference =
    id !== null ? (selected?.id === id ? selected : linked?.id === id ? linked : { id }) : null;
  return (
    <div className="page-stack">
      <SectionHeading
        eyebrow="LOCAL API · READ ONLY"
        title="AI 추천과 처리 기록"
        description="저장된 추천·당시 후보·승인·체결을 분리하여 조회합니다. 주문이나 승인은 실행하지 않습니다."
      />
      <Panel
        title="추천 목록"
        description="추천 생성 시각 최신순 · 서버 조건에 맞는 추천 수 · 전체 분석 실행 건수와 다릅니다"
      >
        <form
          className="overview-controls"
          onSubmit={(e) => {
            e.preventDefault();
            try {
              const from = kstInstant(draft.from),
                to = kstInstant(draft.to);
              if (from && to && from > to) throw new Error();
              const market = draft.market.trim().toUpperCase();
              if (market && (market.length > 30 || !/^[A-Z0-9]+-[A-Z0-9]+$/.test(market)))
                throw new Error();
              setFilter({ ...initial, ...draft, market, from, to });
              setInputError(false);
            } catch {
              setInputError(true);
            }
          }}
        >
          <label>
            시작 · KST
            <input
              className="field"
              type="datetime-local"
              aria-label="추천 기간 시작 · KST"
              value={draft.from}
              onChange={(e) => setDraft({ ...draft, from: e.target.value })}
            />
          </label>
          <label>
            종료 · KST
            <input
              className="field"
              type="datetime-local"
              aria-label="추천 기간 종료 · KST"
              value={draft.to}
              onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            />
          </label>
          <label>
            마켓
            <input
              className="field"
              aria-label="추천 마켓"
              placeholder="KRW-BTC · 정확 일치"
              value={draft.market}
              onChange={(e) => setDraft({ ...draft, market: e.target.value })}
            />
          </label>
          <label>
            판단
            <select
              className="field"
              aria-label="추천 판단"
              value={draft.action}
              onChange={(e) => setDraft({ ...draft, action: e.target.value as Search['action'] })}
            >
              <option value="">전체</option>
              {['BUY', 'SELL', 'HOLD'].map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </label>
          <Button type="submit">조건 적용</Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void query.refetch();
            }}
          >
            목록 새로고침
          </Button>
        </form>
        {inputError && (
          <p role="alert">기간 또는 마켓 형식을 확인하세요. 기존 적용 조건을 유지합니다.</p>
        )}
        <AnalysisQuery query={query}>
          {(data) => (
            <>
              <Source data={data} />
              {data.items.length ? (
                <div className="table-scroll">
                  <table className="data-table" aria-label="API AI 추천">
                    <thead>
                      <tr>
                        <th>추천 시각 / ID</th>
                        <th>마켓</th>
                        <th>판단</th>
                        <th>거래 비율</th>
                        <th>Confidence</th>
                        <th>추천 상태</th>
                        <th>상세</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((r) => (
                        <tr key={r.id}>
                          <td>
                            <Time value={r.createdAt} />
                            <div className="small mono">{r.id}</div>
                          </td>
                          <td>{r.market}</td>
                          <td>{r.action}</td>
                          <td>{ratioText(r.tradeRatio)}</td>
                          <td>{ratioText(r.confidence)}</td>
                          <td>{r.status}</td>
                          <td>
                            <Button
                              variant="ghost"
                              aria-label={`${r.id} 추천 상세`}
                              onClick={() => {
                                setSelected({ id: r.id, scope: data.scope });
                                setParams((p) => {
                                  const next = new URLSearchParams(p);
                                  next.set('recommendation', r.id);
                                  return next;
                                });
                              }}
                            >
                              상세
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="조건에 맞는 추천이 없습니다"
                  description="추천 기록 부재이며 분석 실패나 0 체결을 뜻하지 않습니다."
                />
              )}
              <Pages data={data} label="추천" change={(page) => setFilter({ ...filter, page })} />
            </>
          )}
        </AnalysisQuery>
        <p className="small text-muted">
          승인·실행 기록은 상세에서 조회합니다. 승인 필터·임의 정렬·전체 행동별 통계는 현재 API에서
          제공하지 않습니다.
        </p>
      </Panel>
      <DetailDrawer
        sourceLabel="DETAIL VIEW · LOCAL API · READ ONLY"
        open={id !== null}
        onOpenChange={(open) => {
          if (!open)
            setParams((p) => {
              const next = new URLSearchParams(p);
              next.delete('recommendation');
              return next;
            });
        }}
        title="추천 상세 · 조회 전용"
        description="저장된 판단과 검증된 관련 기록"
      >
        {reference && <AnalysisApiDetail key={id} reference={reference} />}
      </DetailDrawer>
    </div>
  );
}
