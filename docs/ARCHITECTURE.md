# 시스템 아키텍처

이 문서는 현재 구현된 component, data flow와 persistence boundary의 source of truth입니다. 운영 절차는 [OPERATIONS.md](OPERATIONS.md), 전략 연구는 [STRATEGY_RESEARCH.md](STRATEGY_RESEARCH.md), 정책 승격은 [POLICY_PROMOTION.md](POLICY_PROMOTION.md)를 참고하세요.

## Application boundary

| 경로 | 상태 | 책임 |
| --- | --- | --- |
| `trading-engine/` | 구현됨 | Python trading, analytics, research engine |
| `backend/` | 구현됨 | Java 21 / Spring Boot Research / Overview / AI Trade Analysis 조회 전용 API |
| `frontend/` | 구현됨 | React/TypeScript DEMO 대시보드, 개발 환경의 opt-in Research / Overview / AI Analysis GET API 조회 |

Root에는 Compose, 운영 shell script, 문서, `.env.example`이 있습니다. Python package는 `trading-engine/crypto_trading_bot`, 실행 module은 `trading-engine/scripts`입니다.

Backend는 Controller → Service → Repository 구조로 Spring Data JPA와 QueryDSL을 사용합니다. 기존 PostgreSQL 스키마의 소유자는 계속 Python/Alembic이며 Java는 `ddl-auto=validate`, SELECT 전용 DB 역할, 읽기 전용 트랜잭션을 사용합니다. 같은 DB의 `auth.accounts`에 저장된 관리자 계정과 연결된 활성 거래 사용자 및 서버의 exchange·quote 범위만 조회합니다. Research, Overview, AI Analysis GET API는 Spring Security 세션 인증을 요구합니다. 기본 바인딩은 localhost이며 외부 공개 시 TLS가 필수입니다. 저장된 Gate 상태는 과거 기록으로만 반환하며 평가·등록·승인·활성화·주문을 하지 않습니다. Production Compose는 변경하지 않았습니다. 자세한 계약은 [Research Read API v1](../backend/docs/RESEARCH_READ_API_V1.md)과 [Overview Read API v1](../backend/docs/OVERVIEW_READ_API_V1.md)을 참고하세요.

Overview는 `portfolio_snapshots`의 `captured_at DESC, id DESC`로 최신 계좌를 선택합니다. Position과 Performance는 부모 snapshot을 JOIN해 소유자와 quote를 검증하며 현재 성과는 정확히 같은 snapshot에 연결된 행만 반환합니다. 오래된 성과로 대체하지 않습니다. 독립적인 `bot_trading_pnl_summaries`는 user/exchange 단위 KRW 회계이며 snapshot 시각이나 quote별 수익률로 해석하지 않습니다. KRW 이외 quote에서는 Bot PnL을 제공하지 않습니다. 추천은 user/exchange와 검증된 Upbit `QUOTE-` prefix로 제한합니다. 모든 지표는 저장 값이고 PARTIAL/null/BASELINE을 보존합니다. Overview JSON의 Long ID와 BigDecimal은 문자열, 시각은 UTC이며 raw AI payload는 읽거나 노출하지 않습니다. 거래소·OpenAI·Telegram 호출은 없습니다.

Frontend Research는 기본 DEMO를 유지하며 개발 환경의 명시적 flag와 사용자 선택으로만 `LOCAL API · READ ONLY` 모드를 사용합니다. 별도의 read model/client/Query/view가 Summary, Candidate list/detail, Snapshot, Automation checkpoints를 조회합니다. Vite는 허용된 업무 GET 및 인증 GET/POST만 `127.0.0.1:8080`으로 전달합니다. 로그인 화면이 비밀번호를 제출하면 Spring Security가 세션을 생성하며 이후 same-origin HttpOnly 쿠키로 인증합니다. 비밀번호를 영구 저장하거나 Authorization header를 생성하지 않습니다. CSRF 보호를 사용하며 업무 쓰기는 계속 거부합니다. Production build에서는 이 모드를 허용하지 않습니다.

Research 응답은 raw text에서 lossless JSON 파싱·스키마 검증을 거쳐 Long/BigDecimal을 문자열로 보존합니다. 현재 연구 Gate·연구 성과·승인·activation·Runtime·worker 상태는 미제공이며 저장된 과거 기록으로 이를 추정하지 않습니다. API 오류를 DEMO 값으로 대체하지 않습니다. AI Analysis / Portfolio / Operations 및 각 DEMO 모드는 그대로 Mock repository를 사용합니다. 실행·인증·검증 경계는 [Frontend README](../frontend/README.md)에 설명합니다.

Frontend Overview는 별도 `VITE_ENABLE_OVERVIEW_API=true`와 화면 선택으로 활성화합니다. Vite는 Overview 4개 GET만 추가 허용하며 인증·고정 loopback 경계는 동일합니다. Summary 이후 Positions를 조회하고 scope/snapshot ID/관측 시각을 대조합니다. Snapshot 불일치는 1회 재조회 후 갱신 필요 상태로 표시하며 서로 다른 계좌 자료를 혼합하지 않습니다. 성과 이력과 추천은 독립 Query로 기간·action·page 조건을 포함합니다. 금융 값은 문자열과 BigInt로 표시하고 차트 좌표만 Number로 변환합니다. 저장된 성과 index를 사용하며 PARTIAL·새 BASELINE·연속성 미확인 구간은 연결하지 않습니다. 현재 페이지를 전체 기간으로 표현하지 않으며 전일 증감·worker·현재 정책 등 미제공 값은 DEMO로 채우지 않습니다.

AI Trade Analysis는 별도 ADMIN GET 3개로 추천 목록·상세·당시 후보를 읽습니다. 추천과 분석 실행의 소유권을 함께 검증하고, 선택 후보 FK가 가리키는 Universe 실행의 user/pipeline/type/status와 후보 exchange/quote/market을 확인합니다. 같은 pipeline의 최신 실행으로 대체하지 않습니다. 저장된 최종 판단 사유, 승인 기록, MOCK/LIVE 주문 시도와 정규화 체결 요약은 분리합니다. feature JSON은 허용한 시간봉·지표만 projection하며 raw AI/거래소 응답, callback token, Telegram 식별정보는 노출하지 않습니다. 기존 10개 테이블 읽기 권한에 관련 5개 테이블의 최소 컬럼 SELECT만 추가하고 DB 쓰기·외부 API·재계산은 없습니다. Frontend AI Analysis는 기본 DEMO와 독립적인 로컬 API 모드를 제공합니다. 추천 ID 상세와 검증된 당시 후보를 조회하고 응답 간 scope/Universe 연결을 검증합니다. 승인·주문·취소 동작은 없으며 읽기 품질과 데이터 품질을 구분합니다. 계약과 권한은 [AI Trade Analysis Read API v1](../backend/docs/AI_TRADE_ANALYSIS_READ_API_V1.md)을 참고하세요.

## Package structure

| Package | 책임 |
| --- | --- |
| `analysis/` | indicator와 교체 가능한 ranking policy |
| `ai/` | OpenAI Structured Output와 response parsing |
| `config/` | Pydantic Settings와 file secret resolution |
| `db/` | SQLAlchemy model, session, advisory lock |
| `exchange/` | exchange market-data contract, Upbit adapter/client |
| `market_data/` | CoinGecko, Fear & Greed client |
| `notification/` | Telegram message와 callback integration |
| `operational/` | 안전한 오류 분류와 pipeline reporting |
| `services/` | universe, recommendation, order, analytics, research, policy lifecycle |

Market-data 분석 계층은 `ExchangeMarketDataProvider` contract를 사용하고 현재 adapter는 Upbit입니다. LIVE order execution은 의도적으로 Upbit 전용이며 market-data abstraction을 주문 계층까지 과도하게 확장하지 않습니다.

## Analysis pipeline

`scripts.run_ai_trade_analysis`는 실행마다 UUID `pipeline_run_id`를 만들고 다음 subprocess에 전달합니다.

```mermaid
flowchart TD
    A[collect_account_snapshots]
    B[build_market_universe]
    C[capture_portfolio_valuation]
    D[generate_ai_trade_recommendations]
    E[send_latest_ai_trade_recommendations]
    A --> B --> C --> D --> E
```

각 단계가 실패하면 이후 단계는 실행하지 않습니다. 오류는 safe envelope로 분류해 operational alert를 저장·전달하며 자동 재주문이나 자동 주문 취소를 하지 않습니다. Scheduler는 PostgreSQL advisory lock으로 복수 instance의 동시 실행을 막습니다.

### Pipeline identity

`AnalysisRun.pipeline_run_id`, snapshot, universe candidate와 recommendation 관계가 한 실행의 데이터를 고정합니다. Dynamic recommendation은 persisted universe candidate를 참조합니다. 다른 pipeline의 최신 snapshot을 임의로 섞는 대신 user, exchange, market과 pipeline identity를 함께 조회합니다.

## Market Universe

### STATIC

기본 모드입니다. 기존 `ALLOWED_MARKETS`를 분석 및 LIVE allowlist safety restriction으로 유지합니다. 코드 배포만으로 DYNAMIC으로 전환되지 않습니다.

### DYNAMIC

1. Upbit의 quote asset 전체 market과 ticker를 조회합니다.
2. 거래지원, warning/caution, blocklist와 최소 24시간 KRW 거래대금을 검사합니다.
3. `acc_trade_price_24h` 기준 liquidity prefilter를 만듭니다.
4. prefilter와 실제 holdings의 합집합에 대해 orderbook과 Multi-Timeframe data를 수집합니다.
5. `in_prefilter`, `buy_eligible`, 하나 이상의 sufficient timeframe을 만족하는 후보만 ranking합니다.
6. Top N을 `RANKED`, 밖에 남은 holdings를 `HELD`로 최종 context에 추가합니다.

Holding은 `balance + locked > 0`이고 quote asset 자체는 제외합니다. Warning/caution holding은 신규 BUY가 차단되지만 거래지원과 ticker가 유효하면 SELL 검토에 남습니다. 거래지원이 끝난 자산은 source position으로 추적하되 주문 eligibility는 fail-closed합니다.

### Multi-Timeframe features

`MarketCandle`은 `(exchange, market, candle_type, candle_unit, candle_at)` unique identity를 사용합니다.

| Label | Upbit candle | 저장 표현 |
| --- | --- | --- |
| `15m` | minute 15 | `MINUTE / 15` |
| `60m` | minute 60 | `MINUTE / 60` |
| `240m` | minute 240 | `MINUTE / 240` |
| `1d` | day | `DAY / 1` |

Candle은 시간 오름차순으로 indicator에 전달합니다. Summary는 SMA/EMA 5·20, RSI 14, recent change, volume ratio, trend, MACD 12/26/9, ATR 14/percentage, realized volatility, max drawdown을 담습니다. Raw candle 전체를 OpenAI에 보내지 않습니다. 일부 timeframe 실패는 `PARTIAL`, 전부 부족한 신규 후보는 ranking 제외이며 holding은 `HELD`로 보존될 수 있습니다.

### Ranking policy

`MarketRankingPolicy` protocol과 `HeuristicMarketRankingPolicy`가 분리되어 있습니다. Liquidity percentile, trend alignment, momentum, volume confirmation, spread, volatility, drawdown을 bounded component로 결합합니다. Weight와 reference parameter는 policy object에 모이며 snapshot의 `policy_data`와 signature에 저장됩니다. 점수는 candidate reduction heuristic이지 profitability claim이 아닙니다.

`LiveRankingPolicyResolver`는 활성 Full LIVE policy가 없으면 BASELINE을 반환합니다. 활성 정책이 있으면 immutable activation/approval/candidate provenance를 검증한 뒤 해당 weight를 다음 universe build에 제공합니다.

## Recommendation context

`MarketDataContextService`는 candidate feature에 CoinGecko와 Fear & Greed context를 보강합니다. Explicit CoinGecko identity가 없는 동적 symbol을 추측하지 않으며 unavailable data는 나머지 context를 무효화하지 않습니다.

OpenAI advisor는 JSON schema로 `action`, `exchange`, `market`, `trade_ratio`, confidence와 설명을 받습니다. Application guard는 candidate 밖 선택, eligibility 위반, non-finite/out-of-range ratio, insufficient candle과 invalid sizing을 `HOLD`로 바꿉니다.

## Approval과 order execution

```mermaid
sequenceDiagram
    participant P as Analysis pipeline
    participant DB as PostgreSQL
    participant TG as Telegram
    participant EX as Execution service
    participant U as Upbit
    P->>DB: recommendation + BUY/SELL approval request
    P->>TG: recommendation message
    TG->>EX: validated callback decision
    EX->>DB: validate approval and idempotency
    EX->>EX: mode, limits, market, balance guards
    alt MOCK
        EX->>DB: mock attempt/order
    else LIVE and enabled
        EX->>U: optional Order Chance GET
        EX->>U: create order once
        EX->>DB: attempt/order audit
    end
```

LIVE safety는 Telegram approval, `AI_APPROVAL`, `ORDER_EXECUTION_MODE=LIVE`, explicit enablement와 confirmation, Upbit exchange, allowed/persisted candidate, side eligibility, finite ratio, per-order/daily BUY limit을 요구합니다. BUY는 market warning/caution을 실행 직전에 다시 확인합니다. SELL은 경보만으로 막지 않지만 현재 ticker와 balance를 다시 검증합니다.

Recommendation identifier와 execution attempt unique state가 duplicate order를 막습니다. Timeout이나 ambiguous response에서 동일 identifier 조회로 상태를 조정하며 확인되지 않으면 `LIVE_UNKNOWN`으로 남겨 사람의 확인을 요구합니다.

## Reconciliation과 execution ledger

Reconciliation worker는 기존 order의 GET만 수행합니다. 새 주문, retry, cancel은 하지 않습니다. Upbit 상태는 WAIT/DONE/CANCEL과 체결량을 함께 해석해 partial cancel도 실제 체결과 구분합니다.

Telegram approval callback은 random callback token, Telegram chat ID와 message ID를 검증합니다. Cryptographic signature 기반 approval protocol로 설명하지 않습니다.

`OrderLog`는 order-level summary와 raw response audit을 유지하고 `OrderFill`은 fill identity unique constraint로 중복 insert를 막습니다. Quantity, funds, average price는 같은 response의 coherent pair가 있을 때 한 세트로 갱신하며 서로 다른 reconciliation 시점의 값을 섞지 않습니다.

## Analytics architecture

### Recommendation과 research outcome

Recommendation Outcome은 final recommendation 뒤 시장 움직임을, Research Candidate Outcome은 replay ranking pool의 미래 gross market movement를 평가합니다. 시작 가격과 target 시각, 완전히 닫힌 candle 규칙을 고정하며 실제 fill이나 비용을 뜻하지 않습니다.

### Account Activity와 cash flow

Account Activity Sync는 Upbit closed orders/deposits/withdrawals를 read-only로 가져와 UUID 기반 source ledger에 upsert합니다. Overlap sync와 안정적인 pagination으로 늦게 도착한 activity를 다시 확인합니다. 완료 deposit/withdrawal의 performance event time은 `completed_at`이며 없으면 `occurred_at`으로 추측하지 않고 `PARTIAL`입니다.

### Bot Trading PnL

LIVE execution/fill ledger를 source로 bot-attributed inventory lot과 FIFO realized PnL을 계산합니다. 기존 order `amount_krw`, quantity, price의 실행 의미를 변경하지 않으며 account의 외부 activity와 구분합니다.

### Portfolio Valuation과 Performance

Same-pipeline Upbit price를 우선합니다. Upbit pair가 없는 held asset은 explicit CoinGecko mapping이 있을 때만 KRW price fallback을 사용할 수 있고 symbol 자동 추론은 하지 않습니다. 명시적 제외 정책이나 평가 실패는 provenance와 status로 남습니다.

Portfolio Performance는 연속 COMPLETE snapshot 사이의 외부 cash flow를 `(previous, current]` 구간으로 한 번만 포함해 Modified Dietz return을 계산합니다. PARTIAL gap 뒤 최초 COMPLETE는 새 baseline index 100으로 시작하며 과거 row를 고치지 않습니다.

## Runtime isolation

Compose의 worker별 secret mount는 필요한 범위로 제한됩니다.

| Worker | Network/API | Secret scope |
| --- | --- | --- |
| Account Activity | Upbit authenticated read | DB + Upbit |
| Bot Trading PnL | 없음, DB-only | DB |
| Portfolio Performance | 필요 시 Upbit public 1-minute candle | DB |
| Operational Alert | Telegram | DB + Telegram |
| Recommendation Outcome | Upbit public market data | DB |
| Reconciliation | Upbit authenticated order GET | application secret set |

Portfolio Performance는 non-KRW completed external cash flow를 평가할 때 public candle을 조회할 수 있지만 Upbit private API, OpenAI와 Telegram은 사용하지 않습니다. Compose secret scope는 DB secret only입니다.

여러 worker는 container가 상시 존재해도 enable flag가 false이면 API/DB polling 없이 idle합니다. 이 분리는 완전한 zero-trust를 주장하지 않지만 불필요한 credential 전달을 줄입니다.

## Persistence와 provenance

주요 persisted aggregate에는 input identity, schema/policy version, timezone-aware timestamp, watermark/as-of/ceiling과 canonical signature가 포함됩니다. Research와 policy service는 저장 row를 다시 사용할 때 signature와 lineage를 fail-closed로 검증합니다. Preview/apply CLI는 가능한 경우 사람이 확인한 exact signature를 apply에 요구해 preview 이후 source 변경을 탐지합니다.

Migration history에는 제거된 Canary schema가 남아 있지만 cleanup migration 이후 현재 runtime/service에는 Canary promotion flow가 없습니다. 과거 migration은 재현 가능한 schema history이며 현재 기능 목록이 아닙니다.
