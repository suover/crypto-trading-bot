# Overview Read API v1

Python 엔진이 PostgreSQL에 저장한 계좌 평가, 보유 자산, Modified Dietz 성과, FIFO Bot PnL과 추천을 읽는 관리자 전용 API입니다. 금융 지표 재계산, 외부 API 호출, 주문·승인·등록·LIVE 활성화는 수행하지 않습니다. React Overview는 계속 DEMO이며 이 API와의 연결은 별도 작업입니다.

## 실행과 인증

Repository root에서 `cd backend` 후 Java 21 / Gradle Wrapper를 사용합니다. DB 설정, 실행 방법 및 DBA용 최소 권한 SQL은 [Backend README](../README.md)를 따릅니다. PostgreSQL 스키마의 소유자는 Python/Alembic이며 `ddl-auto=validate`를 유지합니다.

모든 endpoint는 기존 stateless HTTP Basic 관리자 인증과 ADMIN 권한을 요구합니다. 기본 주소는 `http://127.0.0.1:8080`입니다. 인터넷 공개 전 TLS와 접근 통제를 별도로 검토해야 합니다. CORS를 확장하거나 인증정보를 frontend에 저장하지 않습니다.

```bash
# 사용자 이름만 전달하고 비밀번호는 curl의 대화식 입력으로 제공합니다.
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/overview/summary"
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/overview/positions"
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/overview/performance?from=2026-01-01T00:00:00Z&to=2026-02-01T00:00:00Z&page=0&size=90"
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/overview/recommendations?action=BUY&page=0&size=10"
```

## 범위와 공통 응답 계약

`BACKEND_TRADING_USER_ID`, `BACKEND_EXCHANGE`, `BACKEND_QUOTE_ASSET`으로 서버 범위를 정합니다. 요청으로 변경할 수 없으며, 설정한 사용자가 없거나 비활성이면 403입니다. `scope`는 `{ "userId": "1", "exchange": "UPBIT", "quoteAsset": "KRW" }` 형태입니다.

- 모든 Long ID는 JSON 문자열입니다. JS safe integer를 넘는 값도 보존합니다.
- 금액·수량·가격·비율·성과 지표는 `BigDecimal.toPlainString()` 문자열 또는 `null`입니다. 지수 표기와 double 변환을 사용하지 않습니다.
- count, page, size, totalElements, totalPages는 JSON 정수입니다.
- 시각은 UTC ISO-8601 문자열입니다. `generatedAt`은 응답 생성 시각이며 저장 데이터의 관측·계산 시각을 대체하지 않습니다.
- 저장된 실제 0과 데이터 없음 `null`을 구분합니다. DB 장애는 빈 결과가 아닌 500입니다.
- Percentage 필드는 저장된 백분율 값 그대로입니다. 예를 들어 `"1.2500000000"`은 1.25%이며 추가로 100을 곱하지 않습니다. `confidence`, `tradeRatio`는 저장된 ratio입니다.
- Entity나 raw AI JSON, 원문 reason, account raw response, signature, secret을 응답하지 않습니다.
- 읽기 전용 REPEATABLE_READ 트랜잭션으로 한 응답의 목록·개수·연결 snapshot을 읽습니다. 서로 다른 API 요청 사이에 snapshot이 바뀔 수 있으므로 ID와 기준 시각을 확인하세요.

기존 Research API의 JSON 숫자 계약은 변경하지 않습니다. Overview의 문자열 계약과 구분하세요.

## 1. `GET /api/v1/overview/summary`

Query parameter는 없습니다. 응답 필드는 `scope`, `portfolio`, `performance`, `botPnl`, `generatedAt`입니다.

### Portfolio

`portfolio_snapshots`를 user/exchange/quote로 제한하고 `captured_at DESC, id DESC`의 첫 행을 사용합니다. ID 최댓값만으로 최신을 판단하지 않습니다. 없으면 `portfolio=null`입니다.

| 필드 | 의미 |
| --- | --- |
| snapshotId, capturedAt | 선택된 snapshot ID 및 관측 시각 |
| valuationStatus | 저장된 COMPLETE/PARTIAL 상태 |
| totalValueKrw | 전체 평가가 성립한 경우의 총액, 불완전하면 null 가능 |
| knownTotalValueKrw | 평가할 수 있는 부분의 금액, 전체 총액을 대체하지 않음 |
| pricedPositionsValueKrw, cashTotalKrw | 저장된 평가 가능 position 금액과 현금 총액 |
| unrealizedPnlKrw | 저장된 계좌 평가손익, null 보존 |
| positionCount, unpricedAssetCount, missingCostBasisCount | 저장된 자산/평가 불가/원가 미확인 개수 |

### Performance

선택된 portfolio와 **동일한 `portfolio_snapshot_id`**의 `portfolio_performance_snapshots`만 반환합니다. Performance의 user/exchange 및 부모의 user/exchange/quote도 검증합니다. 없으면 `performance=null`이며 과거 성과로 대체하지 않습니다. 필드는 아래 성과 이력의 item과 같습니다.

BASELINE은 기준점이며 저장된 누적 0을 검증 완료된 수익률로 해석하지 않습니다. PARTIAL은 성과 자료가 불완전한 상태입니다. COMPLETE, BASELINE, PARTIAL 및 safeReason을 그대로 전달하고 Modified Dietz나 cash flow를 Java에서 재계산하지 않습니다.

### Bot PnL

`bot_trading_pnl_summaries`의 user/exchange 단위 저장 요약을 독립적으로 읽습니다. 최신 portfolio와 같은 시각의 값이라고 가정하지 않습니다. 행이 없거나 서버 quote가 KRW가 아니면 `botPnl=null`입니다.

| 필드 | 의미 |
| --- | --- |
| scope | userId, exchange, amountCurrency=`KRW`; quote별 집계라는 뜻이 아님 |
| accountingStatus | 저장된 COMPLETE/PARTIAL 회계 상태 |
| recognizedRealizedPnlKrw | bot LIVE fill의 입증된 FIFO 매칭분 실현손익 |
| recognizedRealizedReturnPercentage | 해당 인식 손익의 저장 수익률 |
| totalFeesKrw | 저장된 수수료 |
| processedOrderCount, fullyMatchedSellCount, partiallyMatchedSellCount | 처리/완전 매칭/부분 매칭 개수 |
| unmatchedSellCount, incompleteOrderCount | 미매칭/불완전 자료 개수 |
| calculatedAt, sourceLastUpdatedAt | 요약 계산 시각과 원본 최종 갱신 시각 |

Bot PnL은 전체 계좌 수익률이나 평가손익이 아닙니다. PARTIAL의 인식 손익이 존재해도 완전한 회계로 표시하지 않습니다. Portfolio 금액과 임의로 합산하지 않습니다.

## 2. `GET /api/v1/overview/positions`

Query parameter는 없습니다. 응답은 `scope`, `portfolio`, `items`, `generatedAt`입니다. Portfolio metadata는 summary와 동일한 구조입니다.

최신 부모 snapshot에 속한 `portfolio_position_snapshots`만 반환합니다. 부모 user/exchange/quote와 자식 exchange를 확인하며 다른 snapshot의 position을 섞지 않습니다. 정렬은 `market_value_krw DESC NULLS LAST, currency ASC, id ASC`입니다.

Item 필드:

```text
id, portfolioSnapshotId, currency, market,
availableQuantity, lockedQuantity, totalQuantity,
avgBuyPrice, markPrice, marketValueKrw, estimatedCostBasisKrw,
unrealizedPnlKrw, unrealizedPnlPercentage, valuationStatus, priceSource
```

UNPRICED 자산과 null market/price/cost basis를 제거하거나 0으로 바꾸지 않습니다. 저장된 상태와 가격 출처를 그대로 전달합니다. Snapshot이 없으면 `portfolio=null, items=[]`; snapshot이 있으나 position이 없으면 metadata와 `items=[]`입니다. 비정상적으로 큰 응답은 1,000개 초과 시 500으로 실패하며 일부 행만 정상 결과처럼 반환하지 않습니다.

## 3. `GET /api/v1/overview/performance`

| Parameter | 기본값 / 제한 |
| --- | --- |
| from, to | 선택적 ISO-8601 Instant; `from <= to` |
| page | 0, 음수 금지 |
| size | 90, 1–200 |

필터 대상은 **period_end_at**이며 `from <= period_end_at <= to`로 양쪽 경계를 포함합니다. 이는 조회 필터이며 Python cash-flow period inclusion 규칙을 변경하는 것이 아닙니다. `period_end_at DESC, id DESC`로 정렬합니다. 부모 portfolio를 JOIN하여 quote와 양쪽 소유자/exchange를 검증합니다.

응답은 `scope`, `items`, `page`, `size`, `totalElements`, `totalPages`, `generatedAt`입니다. 빈 결과는 `items=[]`, `totalElements=0`, `totalPages=0`이며 마지막 페이지 이후 조회에서도 실제 전체 개수는 유지합니다.

Item 필드:

```text
id, portfolioSnapshotId, periodStartAt, periodEndAt, returnMethod,
periodReturnPercentage, cumulativeReturnPercentage, performanceIndex,
drawdownIndex, drawdownPercentage, maxDrawdownPercentage,
performanceStatus, safeReason, calculatedAt, endValueKrw
```

BASELINE의 periodStartAt/periodReturnPercentage 또는 PARTIAL의 성과 값은 null일 수 있습니다. Index, drawdown, MDD도 저장 값 그대로이며 Java에서 재계산하지 않습니다.

## 4. `GET /api/v1/overview/recommendations`

| Parameter | 기본값 / 제한 |
| --- | --- |
| action | 생략 시 전체; BUY / SELL / HOLD만 허용 |
| page | 0, 음수 금지 |
| size | 10, 1–50 |

`trade_recommendations`의 user/exchange와 검증된 Upbit `QUOTE-` 접두어를 사용합니다. KRW 설정에서 `BTC-ETH`, `XKRW-BTC`, 빈 base인 `KRW-`는 포함하지 않습니다. 현재 지원되지 않는 다른 거래소의 market 표기법을 추론하지 않으며 해당 추천 조회는 빈 결과입니다. 정렬은 `created_at DESC, id DESC`이고 page envelope는 performance와 같습니다.

Item은 `id`, `analysisRunId`, `market`, `action`, `confidence`, `tradeRatio`, `aiModel`, `status`, `createdAt`입니다. HOLD의 nullable ratio/model 등 저장 값을 보존합니다. 추천 status는 체결 증명이 아니며 이 API가 주문 상태를 판정하지 않습니다. Confidence는 검증된 매매 성공 확률이 아니고 tradeRatio는 실제 체결 비율이 아닙니다. `analysisRunId`는 FK 값만 읽으므로 `analysis_runs` SELECT 권한이 필요하지 않습니다.

## 오류와 조회 상한

두 page API의 `page * size`는 최대 1,000,000입니다. 범위 초과·정수 overflow·잘못된 날짜·역전된 기간·빈 값·알 수 없는 action·미정의/중복 query parameter는 400입니다. 서버 범위나 임의 sort를 요청으로 바꿀 수 없습니다.

| 상태 | 의미 |
| --- | --- |
| 200 | 정상 조회, 데이터가 없어도 nullable object / 빈 목록 |
| 400 INVALID_REQUEST | 잘못된 조회 조건 |
| 401 UNAUTHORIZED | 미인증 / 잘못된 인증 |
| 403 FORBIDDEN | ADMIN 권한 없음, 설정 사용자가 없거나 비활성, 인증된 쓰기/미정의 경로 요청 |
| 500 INTERNAL_ERROR | DB 장애 등 내부 실패; 빈 데이터로 숨기지 않음 |

오류 JSON은 기존 `code`, `message`, `generatedAt`입니다. SQL, JDBC 연결정보, stack trace를 노출하지 않습니다. GET 4개만 허용하며 POST/PUT/PATCH/DELETE 또는 승인·실행 경로를 추가하지 않습니다.

## 검증과 운영 경계

Mockito 서비스 테스트, MockMvc/Security 계약 테스트 및 실제 Alembic PostgreSQL 17 Testcontainers 테스트로 범위·FK·최신 순서·PARTIAL·nullable·페이지·날짜·큰 ID/Decimal 문자열을 검증합니다. 기존 Research 테스트도 유지합니다.

테스트용 SELECT 계정은 Overview 5개 테이블과 기존 Research 5개 테이블에만 접근합니다. JDBC read-only flag를 해제한 연결에서도 INSERT/UPDATE/DELETE/TRUNCATE/CREATE/TEMP/ALTER/DROP 및 불필요한 테이블 SELECT가 거부되는지 검증합니다. 운영 권한을 변경하거나 운영 DB를 테스트에 사용하지 않습니다.

기존 index/schema를 사용하며 migration을 추가하지 않습니다. 운영 데이터 규모에 따른 쿼리 성능 측정은 별도 과제입니다. 현재 API의 조회 상한은 무한 응답을 막지만 대규모 count 비용까지 없애지는 않습니다. Production Compose, Python 저장/계산 정책, LIVE safety와 frontend 코드는 변경하지 않습니다.
