# AI Trade Analysis Read API v1

기존 Python/Alembic PostgreSQL 기록을 읽는 관리자용 API입니다. 추천 목록, 저장된 최종 판단 근거, 검증된 당시 Universe, 승인·주문 시도·정규화된 체결 요약을 제공합니다. 외부 API 호출, 판단/지표 재계산, 승인, 주문, 취소, 재시도, reconciliation은 수행하지 않습니다.

## 실행과 접근 경계

Repository root에서 `cd backend` 후 [Backend README](../README.md)의 로컬 PostgreSQL 및 관리자 설정으로 실행합니다. 모든 endpoint는 기존 Spring Security 세션 ADMIN 인증과 서버에 설정된 active user/exchange/quote scope를 요구합니다. Frontend는 개발 환경의 명시적 opt-in으로 이 GET 3개만 로컬 Vite proxy를 통해 조회합니다. 실행 절차는 [Frontend README](../../frontend/README.md#ai-analysis-local-api-v1)를 참고하세요. 운영 DB·권한·secret 설정은 자동 변경하지 않습니다.

`@Transactional(readOnly=true, isolation=REPEATABLE_READ)`, SELECT-only Repository, `@Immutable`, `ddl-auto=validate`를 유지합니다. 한 HTTP 요청 안의 count/items/관련 기록은 같은 DB snapshot을 사용합니다. 별도 HTTP 요청 사이의 동일 snapshot은 보장하지 않으며 각 응답에 `generatedAt`을 제공합니다.

## Endpoint

공통 prefix: `/api/v1/ai-trade-analysis/recommendations`.

| GET | 조건 | 응답 |
| --- | --- | --- |
| prefix 자체 | from, to, market, action, page, size | 추천 Page |
| `/{id}` | query parameter 없음 | Detail |
| `/{id}/candidates` | page, size | 검증된 Universe의 Candidate Page |

- page 기본 0, size 기본 20, size 1–100, page × size 최대 1,000,000.
- from/to는 offset을 포함한 ISO-8601 instant이며 `created_at >= from AND created_at <= to`. 역전 기간은 400.
- action은 BUY/SELL/HOLD, market은 대문자 QUOTE-BASE의 정확 일치입니다. 다른 quote 요청은 서버 scope를 넓히지 않고 빈 목록입니다.
- 추천은 `created_at DESC, id DESC`, 후보는 `rank ASC NULLS LAST, id ASC`.
- ID는 양의 Java Long 범위의 canonical decimal입니다. 0, 음수, 앞자리 0, overflow는 400.
- 미정의/중복/빈 parameter, scope 변경, 임의 sort는 400. 승인 상태 필터는 v1에 없습니다.
- 현재 입증된 market 표기법은 UPBIT QUOTE-BASE입니다. 다른 exchange 추천은 반환하지 않습니다.

공통 `scope`는 userId/exchange/quoteAsset, Page는 items/page/size/totalElements/totalPages/generatedAt입니다. Long ID와 모든 BigDecimal은 **문자열**, 시각은 UTC instant, count는 JSON 정수입니다. Frontend는 큰 정수 count를 lossless parsing해야 합니다. null은 저장값 부재이며 0과 구분합니다.

조회 예시 경로: `/api/v1/ai-trade-analysis/recommendations?action=HOLD&page=0&size=20`.
[관리자 세션 로그인](../README.md#세션과-인증-api) 이후 동일 브라우저의 frontend origin에서 조회합니다. HTTP Basic `curl --user` 방식은 지원하지 않습니다.

## Recommendation과 Detail

목록 Recommendation:
id, analysisRunId, market, action, tradeRatio, confidence, aiModel, status, createdAt, updatedAt.

Detail:
scope, recommendation, analysis, finalDecisionReason, recommendedAmountKrw, recommendedQuantity, universe, selectedCandidate, approvals, executionAttempts, order, generatedAt.

- `analysis`: id, pipelineRunId, runType, tradingMode, status, startedAt, finishedAt.
- `finalDecisionReason`은 Python이 안전 규칙 적용 후 저장한 최종 사유입니다. 순수 AI 원본 판단이라고 해석하지 않습니다. raw AI response/context 및 safety override 내부 JSON은 반환하지 않습니다.
- recommendedAmountKrw/recommendedQuantity는 추천 당시 애플리케이션의 저장 sizing입니다. 실제 주문·체결 금액/수량과 구분합니다.
- 설명 문자열은 일반 텍스트로 표시해야 하며 HTML로 실행하지 않습니다.
- 목록/상세 모두 recommendation.user_id와 연결 analysis_run.user_id를 서버 scope와 검증합니다. 다른 사용자/거래소/quote, 잘못된 분석 소유권과 없는 ID는 동일 404입니다.
- HOLD에서 승인/주문이 없을 수 있습니다. 없음은 오류, 거절, 주문 실패 또는 0 체결로 변환하지 않습니다.

## Universe provenance

추천의 `universe_candidate_id` FK가 검증의 시작점입니다. 해당 후보와 그 부모 Universe 실행에 대해 다음을 모두 확인합니다.

- candidate.user_id = universe_run.user_id = configured user
- candidate.exchange/quote_asset = configured exchange/quote
- candidate.market = recommendation.market
- 두 분석 실행의 nonblank pipeline_run_id 일치
- 부모 run_type=MARKET_UNIVERSE, status=SUCCESS

추천 생성 실행 ID와 Universe 실행 ID는 별개입니다. 검증된 **선택 후보의 부모 실행**만 사용하고, 같은 pipeline에 다른 실행이 있더라도 최신 실행으로 바꾸지 않습니다.

| universe.status | 의미 |
| --- | --- |
| VERIFIED | FK와 소유권/pipeline 검증 완료; analysisRunId와 selectedCandidateId 제공 |
| NO_REFERENCE | nullable FK 없음; 과거 STATIC 등을 추측하지 않음 |
| INVALID_REFERENCE | FK는 있지만 연결 검증 실패; 관련 ID/후보 미노출 |

NO_REFERENCE/INVALID_REFERENCE일 때 candidates items는 빈 배열이고 universe.status가 원인을 구분합니다. 빈 배열을 당시 Universe 후보가 0개였다는 뜻으로 해석하지 않습니다. ai_response 또는 현재 latest snapshot에서 대체 복원하지 않습니다.

Candidate는 id/universeAnalysisRunId/market/baseAsset/quoteAsset/rank/score/selectionSource/buyEligible/sellEligible/quoteTradeValue24h/createdAt/features를 제공합니다. HELD와 BUY=false 후보도 유지하고 ranking을 다시 계산하지 않습니다.

## 저장 feature의 제한된 projection

feature_data 전체를 반환하지 않습니다. timeframes 중 15m/1h/4h/1d만 읽고 각 frame의 data_quality, candle_count, trend_label 및 다음 숫자 필드만 선택합니다.

`latest_price, sma_5, sma_20, ema_5, ema_20, rsi_14, recent_change_rate, volume_ratio, macd, macd_signal, macd_histogram, atr_14, atr_percentage, realized_volatility, max_drawdown`

가격은 원본 quote 단위이며 percentage/ratio/volatility 단위도 저장된 그대로입니다. 표시용 단위 변환이나 재계산은 하지 않습니다. JSON 숫자도 BigDecimal로 읽고 문자열로 반환합니다. 최대 80 유효 숫자와 scale 절댓값 100을 허용하고 non-finite/비정상 값은 null 및 invalidFields로 표시합니다.

- features.dataQuality와 frame.dataQuality는 저장된 품질입니다.
- features.projectionStatus는 읽기/파싱 결과 COMPLETE/PARTIAL/UNAVAILABLE입니다. COMPLETE는 매매 데이터가 SUFFICIENT라는 뜻이 아닙니다.
- 없는 timeframe은 생성하지 않습니다. invalidFields는 허용된 필드 이름만 포함합니다.
- malformed JSON/구조, 알려진 timeframe 없음, 1,000,000자를 넘는 payload는 UNAVAILABLE입니다.
- raw candle, position, 임의 JSON key, market_event_data는 노출하지 않습니다.

## 승인·주문 시도·체결 의미

각 영역은 저장 기록 그대로이며 하나의 성공/실패 상태로 합치지 않습니다.

- approvals: id/status/expiresAt/approvedAt/rejectedAt/createdAt/updatedAt. 서버 현재 시각으로 EXPIRED를 재판정하지 않습니다.
- executionAttempts: id/approvalRequestId/orderLogId/tradingMode/attemptNumber/status/errorCode/attemptedAt/nextRetryAt.
- order: id/approvalRequestId/tradingMode/market/side/orderType/status/amountKrw/quantity/price 및 executedQuantity/executedFundsKrw/averageExecutionPrice/paidFee/remainingQuantity/tradesCount/executionSyncedAt/createdAt/updatedAt.
- amountKrw/quantity/price의 기존 의미는 유지하고 executed*와 구분합니다. 평균가·수수료·수량을 다른 필드에서 보충 계산하지 않습니다.
- LIVE_UNKNOWN은 미확정으로 남습니다. LIVE_EXECUTED_CANCELLED의 양수 체결은 취소된 체결이 아니며 미체결 잔량 취소 상태와 구분해야 합니다.
- MOCK/LIVE를 합산하지 않습니다. 분석의 AI_APPROVAL은 주문 tradingMode를 대신하지 않습니다.
- 승인·시도는 시각/ID 오름차순으로 반환하고 각각 최대 1,000건입니다. 초과하면 500으로 실패하며 일부를 전체로 표시하지 않습니다.
- 모든 자식의 user/recommendation 관계를 검증합니다. Order는 exchange/market도 확인하며, nullable approval FK가 있으면 동일 추천·사용자의 승인이어야 합니다. Attempt의 approval과 연결 order의 소유권·market·mode·approval도 일치해야 합니다.
- 잘못된 자식 연결은 반환하지 않습니다. 빈 결과는 검증된 관련 기록을 찾지 못했다는 뜻이며 실제 주문이 없었다는 증명이 아닙니다.

callback_token, Telegram chat/message ID, raw AI/exchange payload, exchange_order_id, 자유형 error_message는 DTO에 없습니다. 개별 fill, FIFO 매칭, Outcome 성과, worker 상태는 v1 범위 밖입니다.

## 오류·DB 권한·검증

400=입력 오류, 401=미인증, 403=관리자 권한/active user 불충족, 404=없거나 범위 밖 추천, 500=DB 장애/조회 상한 초과 등입니다. 실패를 빈 성공 결과로 대체하지 않으며 SQL/JDBC/stack trace는 반환하지 않습니다.

[Backend README](../README.md)의 기존 권한에 아래 5개 테이블의 매핑 컬럼 SELECT만 추가합니다. 테스트 fixture는 실제 column privilege로 비공개 컬럼 SELECT, DML/DDL/TEMP를 차단합니다. 기존 trade_recommendations 전체 SELECT 권한은 유지되므로 raw ai_response의 DB 권한 차단까지 주장하지 않으며, 이 API의 Entity/DTO에는 매핑하지 않습니다.

기존 Alembic PostgreSQL 17 Testcontainers에서 scope/FK/pipeline/quote, 큰 ID와 JSONB/Decimal, HOLD/legacy/HELD/PARTIAL, partial cancel/UNKNOWN/MOCK, 세션 인증, 쓰기 거부와 query count를 검증합니다. 목록과 후보 페이지는 row별 추가 조회를 하지 않습니다. 기존 index를 사용하며 운영 규모의 count/offset 성능 측정은 별도입니다. migration이나 운영 DB 변경은 없습니다.
