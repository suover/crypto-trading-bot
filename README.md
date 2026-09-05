# crypto-trading-bot

시장 데이터, 계좌 상태, 설정값을 바탕으로 AI 매매 후보를 만들고, 사용자가 Telegram에서 승인한 경우에만 주문 흐름을 진행하는 승인형 코인 트레이딩 봇 프로젝트입니다.

Production은 **예약 AI 분석 → BUY/SELL/HOLD 결정 → BUY/SELL Telegram 승인 요청 → 사용자 승인 → Upbit LIVE 주문** 흐름으로 운영됩니다. AI 분석이 예약 실행되더라도 실제 BUY/SELL 주문은 사용자의 Telegram 승인 없이는 실행되지 않습니다.

## 주요 구성

* Python 3.14
* uv
* PostgreSQL 17
* Docker Compose
* SQLAlchemy / Alembic
* Telegram Bot 연동
* OpenAI API 연동
* 거래소 API 연동 준비
* Ruff / Pytest 기반 품질 검증

## 운영 문서

서버 운영과 제한적 라이브 테스트 절차는 별도 문서를 참고합니다.

* [서버 운영 가이드](docs/OPERATIONS.md)
* [제한적 라이브 테스트 런북](docs/LIVE_RUNBOOK.md)
* [Production scheduled LIVE 운영 및 배포](docs/PRODUCTION_LIVE.md)
* [PostgreSQL 백업 스크립트](scripts/backup_db.sh)
* [서버 런타임 안전 점검 스크립트](scripts/check_server_runtime_safety.sh)

## 실행 서비스

Docker Compose 기준으로 다음 서비스가 실행됩니다.

| 서비스                       | 실행 방식      | 역할                                    |
| ------------------------- | ---------- | ------------------------------------- |
| `postgres`                | 상시 실행      | 로컬 PostgreSQL 17 데이터베이스               |
| `migrate`                 | 1회 실행 후 종료 | Alembic DB 마이그레이션 실행                  |
| `ai-trade-scheduler`      | `scheduler` profile | 설정된 시각에 AI 분석 파이프라인 실행                |
| `telegram-listener`       | 상시 실행      | Telegram 승인/거절 버튼 콜백 수신               |
| `mock-order-retry-worker` | 상시 실행      | 실패한 모의 주문 및 알림 재시도 처리                 |
| `live-order-reconciliation-worker` | 상시 실행 | 기존 LIVE 주문 상태 GET 및 DB 동기화 (MOCK에서는 대기) |
| `account-activity-sync-worker` | 상시 실행 | Upbit 종료 주문·입금·출금 GET 및 source ledger 동기화 (기본 비활성) |
| `bot-trading-pnl-worker` | 상시 실행 | 실제 bot LIVE 체결의 DB-only FIFO 회계 (기본 비활성) |
| `portfolio-performance-worker` | 상시 실행 | 외부 입출금 영향을 제거한 계좌 성과 계산 (기본 비활성) |
| `recommendation-outcome-worker` | 상시 실행 | 추천 이후 1h/4h/24h 시장 결과 평가 (기본 비활성) |
| `ai-trade-analysis`       | 수동 실행      | 시장/계좌/캔들 수집, AI 추천 생성, Telegram 알림 발송 |

기본 서버/프로덕션 유사 런타임에는 `account-activity-sync-worker`도 포함되지만 `ACCOUNT_ACTIVITY_SYNC_ENABLED=false` 기본값에서는 DB/API 접근 없이 대기합니다. 배포만으로 authenticated polling이 시작되지 않습니다. `ai-trade-scheduler`는 기본 런타임에 포함되지 않으며, `scheduler` profile을 명시할 때만 실행됩니다.

`ai-trade-analysis`는 `manual` profile에 포함된 수동 실행 서비스입니다. 즉시 1회 분석을 테스트할 때 사용합니다.

Production scheduled LIVE에서는 기본 런타임에 더해 `scheduler` profile의 `ai-trade-scheduler`를 명시적으로 실행합니다. 배포와 운영 점검 절차는 [Production LIVE 운영 문서](docs/PRODUCTION_LIVE.md)를 따릅니다.

### AI 추천 사후성과 평가

`recommendation-outcome-worker`는 최종 저장된 추천 신호를 실제 주문·체결 여부와
분리해 평가합니다. 시작가는 동일 pipeline의 exact `MarketUniverseCandidate`에 저장된
가격만 사용하고, 종료가는 target 시각까지 완전히 닫힌 Upbit 1분봉 종가만 사용합니다.
BUY는 시장 수익률, SELL은 그 부호를 반전한 action-aligned gross return을 기록합니다.
HOLD는 이후 시장 수익률만 기록하며 WIN/LOSS 적중률로 해석하지 않습니다. 수수료,
스프레드, 슬리피지와 실제 손익은 포함하지 않습니다.

기본 horizon은 60/240/1440분이며 exact universe 후보도 함께 평가합니다. confidence는
모델의 self-report일 뿐 성공 확률이 아닙니다. worker는
`RECOMMENDATION_OUTCOME_ENABLED=false`가 기본이고 DB password와 public Upbit market
data만 사용합니다.

```bash
python -m scripts.rebuild_recommendation_outcomes --limit 10
python -m scripts.rebuild_recommendation_outcomes --limit 10 --apply
python -m scripts.report_recommendation_outcomes
```

첫 명령은 DB write 없는 dry-run이지만 public historical candle 조회는 수행합니다.

### Strategy Replay Dataset

`STRATEGY_REPLAY_DATASET_ENABLED=false`가 기본입니다. 활성화하면 DYNAMIC universe가
이미 수집한 liquidity prefilter와 HELD data-collection 후보, timeframe/orderbook feature,
full ranking 및 final selection provenance를 별도 research 테이블에 저장합니다. 추가 Upbit,
CoinGecko, OpenAI 또는 Telegram 호출은 없으며 기존 `MarketUniverseCandidate`와 GPT 후보는
변경되지 않습니다. 이는 backtest 자체가 아니라 offline replay를 위한 데이터 기반입니다.
배포 이전 후보는 완전하게 복원할 수 없으므로 추측성 backfill하지 않고 활성화 이후부터
정확한 dataset을 축적합니다.

```bash
python -m scripts.report_strategy_replay_dataset --limit 50
```

이 report는 DB-only이며 어떤 row도 수정하지 않습니다.

## 사전 준비

다음 도구가 설치되어 있어야 합니다.

* Python 3.14
* uv
* Docker Desktop
* Git

PostgreSQL은 별도로 로컬에 설치하지 않아도 됩니다. 개발용 PostgreSQL은 Docker Compose의 `postgres` 서비스로 실행합니다.

서버/프로덕션 유사 런타임에서는 PostgreSQL을 호스트 포트로 공개하지 않습니다. 호스트에서 `localhost:5432`로 접속해야 하는 로컬 개발에서만 `docker-compose.local.yml`을 함께 사용합니다.

## 환경 변수 설정

예시 환경 변수 파일을 복사해서 `.env`를 만듭니다.

```powershell
Copy-Item .env.example .env
```

`.env`에는 실행 설정과 비밀 파일 경로를 입력하고, 실제 비밀값은 `.secrets/`의 개별 파일에 저장합니다.

```env
APP_ENV=local

SECRET_DIR=.secrets

DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=crypto_trading_bot
DATABASE_USER=trading_user
DATABASE_PASSWORD_FILE=.secrets/postgres_password

OPENAI_API_KEY_FILE=.secrets/openai_api_key
OPENAI_TRADE_MODEL=gpt-5.6-sol
OPENAI_REASONING_EFFORT=medium

TELEGRAM_BOT_TOKEN_FILE=.secrets/telegram_bot_token
TELEGRAM_CHAT_ID=

UPBIT_ACCESS_KEY_FILE=.secrets/upbit_access_key
UPBIT_SECRET_KEY_FILE=.secrets/upbit_secret_key

UPBIT_ORDERBOOK_ENABLED=true
UPBIT_ORDERBOOK_COUNT=15

COINGECKO_ENABLED=true
COINGECKO_API_BASE_URL=https://api.coingecko.com/api/v3
PORTFOLIO_COINGECKO_ASSET_MAPPING=
PORTFOLIO_EXCLUDED_ASSETS=
COINGECKO_API_KEY=
COINGECKO_REQUEST_TIMEOUT_SECONDS=5

FEAR_GREED_ENABLED=true
FEAR_GREED_API_BASE_URL=https://api.alternative.me
FEAR_GREED_REQUEST_TIMEOUT_SECONDS=5

TRADING_MODE=AI_APPROVAL
ORDER_EXECUTION_MODE=MOCK

MAX_ORDER_AMOUNT_KRW=10000
DAILY_MAX_ORDER_AMOUNT_KRW=30000

ALLOWED_MARKETS=KRW-BTC,KRW-ETH

LIVE_ORDER_ENABLED=false
LIVE_ORDER_CONFIRMATION=

MOCK_ORDER_RETRY_MAX_RETRIES=3
MOCK_ORDER_RETRY_DELAYS_MINUTES=5,15,30

AI_ANALYSIS_SCHEDULER_ENABLED=true
AI_ANALYSIS_SCHEDULE_TIMES=09:00
AI_ANALYSIS_RUN_ON_STARTUP=false
```

비밀값을 저장할 파일을 생성합니다.

```powershell
New-Item -ItemType Directory -Force .secrets
New-Item -ItemType File -Force .secrets\postgres_password
New-Item -ItemType File -Force .secrets\openai_api_key
New-Item -ItemType File -Force .secrets\telegram_bot_token
New-Item -ItemType File -Force .secrets\upbit_access_key
New-Item -ItemType File -Force .secrets\upbit_secret_key
```

각 파일에는 비밀값만 입력합니다.

`.env`와 `.secrets/`는 Git에 포함되지 않습니다. Docker Compose로 실행하면 `.secrets/`의 파일들이 컨테이너 내부 `/run/secrets/` 경로에 연결됩니다.

## 로컬 개발 실행

로컬 개발에서는 PostgreSQL만 Docker로 실행하고, Python 명령은 호스트 환경에서 실행할 수 있습니다.

### 1. PostgreSQL 실행

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d postgres
```

상태 확인:

```powershell
docker compose ps
```

### 2. Python 의존성 설치

```powershell
uv sync --dev --locked
```

### 3. DB 마이그레이션 실행

```powershell
uv run alembic upgrade head
```

### 4. 테스트 실행

```powershell
uv run pytest -q
```

### 5. 코드 포맷/린트 확인

```powershell
uv run ruff format --check .
uv run ruff check .
```

### AI trade sizing

AI는 허용된 마켓, `BUY`/`SELL`/`HOLD`, 그리고 0~1의
`trade_ratio`만 결정합니다. 정확한 BUY KRW 금액과 SELL 코인 수량/평가액은
애플리케이션이 현재 가용 잔고와 유효한 가격으로 계산합니다.
현재 `max_order_amount_krw` 및 `daily_max_order_amount_krw`는 노출을 늘리는
BUY에만 적용됩니다. SELL 청산은 BUY 최대/일일 한도로 차단하지 않지만,
실행 직전 정확한 마켓 ticker, Upbit 최소 주문 금액, 현재 가용 수량을
다시 검증합니다. 모든 BUY/SELL은 Telegram 승인을 계속 필요로 합니다.

## Market Universe와 Multi-Timeframe 분석

기본값인 `MARKET_UNIVERSE_MODE=STATIC`에서는 기존 `ALLOWED_MARKETS`와 DB의
활성 registry 항목을 그대로 분석 및 LIVE 안전 allowlist로 사용합니다. 따라서 코드를
배포하는 것만으로 현재 운영 universe가 바뀌지 않습니다.

`MARKET_UNIVERSE_MODE=DYNAMIC`에서는 Upbit의 현재 KRW 전체 마켓과 ticker를
조회하고, warning/caution, blocklist, 24시간 KRW 거래대금
(`acc_trade_price_24h`)을 적용한 뒤 유동성 prefilter와 교체 가능한 heuristic ranking으로
Top N을 선정합니다. 보유 종목은 Top N 밖이거나 경보 상태여도 SELL 판단을 위해
후보에 남지만 신규 BUY는 차단됩니다. 각 분석은 하나의 `pipeline_run_id`와 persisted
universe candidate를 사용하며 15분, 60분, 240분, 일봉의 compact feature만 GPT에
전달합니다.

```env
MARKET_UNIVERSE_MODE=DYNAMIC
MARKET_UNIVERSE_TOP_N=7
MARKET_UNIVERSE_PREFILTER_N=20
MARKET_UNIVERSE_MIN_24H_TRADE_VALUE_KRW=0
MARKET_BLOCKLIST=
ANALYSIS_TIMEFRAMES=15m,60m,240m,1d
ANALYSIS_CANDLE_COUNT=50
```

주문과 OpenAI 호출 없이 universe/feature 결과만 확인하려면 다음 진단 명령을
사용합니다. 이 명령은 market/candle 데이터와 추적용 universe row를 DB에 저장하지만
추천이나 주문을 만들지 않습니다.

```powershell
uv run python -m scripts.check_market_universe
```

DYNAMIC 추천을 실제 주문으로 실행하려면 기존 LIVE 플래그와 Telegram 승인 외에도
`LIVE_DYNAMIC_MARKET_ENABLED=true`를 별도로 설정해야 합니다. 실행 직전 persisted
candidate와 pipeline identity, BUY/SELL eligibility, KRW quote, 현재 상장 상태,
BUY warning/caution, ticker를 다시 검증합니다. STATIC 모드에서는 이 플래그가 사용되지
않고 기존 `ALLOWED_MARKETS` 제한이 유지됩니다.

## Docker Compose 전체 런타임 실행

서버/프로덕션 유사 런타임을 Docker Compose로 실행합니다. 이 런타임은 PostgreSQL을 공개 포트로 노출하지 않습니다.

```powershell
docker compose up -d --build
```

이 명령은 다음 흐름으로 실행됩니다.

1. `postgres` 컨테이너 실행
2. PostgreSQL healthcheck 통과 대기
3. `migrate` 컨테이너에서 Alembic 마이그레이션 실행
4. 마이그레이션 성공 후 `telegram-listener` 실행
5. 마이그레이션 성공 후 `mock-order-retry-worker` 실행
6. 마이그레이션 성공 후 `live-order-reconciliation-worker` 실행 (MOCK에서는 조회 없이 대기)

`ai-trade-scheduler`는 기본 런타임에서 시작되지 않습니다. 예약 분석을 켜야 할 때만 `scheduler` profile로 명시적으로 실행합니다.

서비스 상태 확인:

```powershell
docker compose ps -a
```

로그 확인:

```powershell
docker compose logs --tail=100 migrate
docker compose logs --tail=100 telegram-listener
docker compose logs --tail=100 mock-order-retry-worker
docker compose logs --tail=100 live-order-reconciliation-worker
```

전체 로그를 따라가려면 다음 명령을 사용합니다.

```powershell
docker compose logs -f
```

## AI 분석 스케줄러

`ai-trade-scheduler`는 `.env`의 `AI_ANALYSIS_SCHEDULE_TIMES`에 설정된 시각마다 AI 분석 파이프라인을 실행합니다.

기본 설정은 하루 1회입니다.

```env
AI_ANALYSIS_SCHEDULER_ENABLED=true
AI_ANALYSIS_SCHEDULE_TIMES=09:00
AI_ANALYSIS_RUN_ON_STARTUP=false
```

하루 2~3회로 늘리고 싶으면 아래처럼 설정합니다.

```env
AI_ANALYSIS_SCHEDULE_TIMES=09:00,15:00,21:00
```

스케줄러는 다음 파이프라인을 실행합니다.

1. 계좌 잔고 스냅샷 수집
2. 동일 pipeline ID로 market universe 및 multi-timeframe 데이터 구축
3. persisted universe로 AI 매매 추천 생성
4. AI 추천 결과 Telegram 알림 및 승인 요청 발송

`BUY` 또는 `SELL` 추천은 Telegram 승인 요청으로 이어지고, 사용자가 승인해야 주문 흐름이 진행됩니다.

`HOLD` 추천은 주문 실행 대상이 아니므로 매매 판단 참고용 알림으로 취급합니다.

스케줄러 명시 실행:

```powershell
docker compose --profile scheduler up -d --build ai-trade-scheduler
```

스케줄러 로그 확인:

```powershell
docker compose --profile scheduler logs --tail=100 ai-trade-scheduler
```

## Production 배포

운영 서버의 clean한 `main`에서 다음 스크립트로 반복 가능한 fail-closed 배포를 수행합니다.

```bash
bash scripts/deploy_production.sh
```

GitHub Actions의 `Deploy Production` workflow는 `workflow_dispatch`로만 실행되며 `DEPLOY` 확인 문자열과 workflow가 검증한 exact main SHA를 요구합니다. 서버 배포 스크립트는 Compose/PostgreSQL/Git 사전검증과 DB/`.env` 백업을 운영 서비스 중지 전에 완료한 뒤 scheduler 중지, fast-forward Git 업데이트, 전체 이미지 build, 짧은 listener/worker 중지, migration, 서비스 재생성, Production safety check 순으로 실행합니다. 자동 Git/DB rollback은 수행하지 않습니다.

Production 상태 점검:

```bash
bash scripts/check_server_runtime_safety.sh --production-live
```

## AI 분석 파이프라인 수동 실행

Docker Compose 런타임이 준비된 상태에서 AI 분석 파이프라인을 1회 실행할 수 있습니다.

```powershell
docker compose --profile manual run --rm ai-trade-analysis
```

이 명령은 다음 단계를 순서대로 실행합니다.

1. 계좌 잔고 스냅샷 수집
2. 동일 pipeline ID로 market universe 및 multi-timeframe 데이터 구축
3. persisted universe로 AI 매매 추천 생성
4. AI 추천 결과 Telegram 알림 및 승인 요청 발송

`BUY` 또는 `SELL` 추천이 생성되면 Telegram 승인 요청이 발송됩니다. 사용자가 승인해야 후속 주문 흐름으로 넘어갑니다.

## 중복 실행 방지

`ai-trade-scheduler`, `telegram-listener`, `mock-order-retry-worker`, `live-order-reconciliation-worker`는 PostgreSQL advisory lock을 사용해 중복 실행을 방지합니다.

이미 같은 프로세스가 실행 중이면 추가 실행된 프로세스는 바로 종료됩니다.

예상 메시지 예시:

```text
Another AI trade scheduler is already running. Scheduler will exit.
```

```text
Another Telegram approval listener is already running. Listener will exit.
```

```text
Another mock order retry worker is already running. Worker will exit.
```

## 종료

컨테이너를 중지하고 제거합니다.

```powershell
docker compose down
```

`docker compose down`은 컨테이너와 네트워크를 제거하지만, PostgreSQL 데이터 볼륨은 유지합니다.

`docker compose down -v`는 `postgres_data` 데이터베이스 볼륨을 삭제합니다. 로컬 DB 데이터까지 초기화해야 할 때만 사용하고, 서버/프로덕션 유사 런타임에서는 가볍게 실행하지 않습니다.

```powershell
docker compose down -v
```

## 모의 주문 재시도 워커

`mock-order-retry-worker`는 실패한 모의 주문 처리와 Telegram 알림 재시도 흐름을 담당합니다.

LIVE 주문 자체는 자동 재시도하지 않으며, 이 worker는 모의 주문과 관련 알림 재시도만 담당합니다.

### 기존 LIVE 주문 자동 상태 추적

`live-order-reconciliation-worker`는 `LIVE_PLACED` / `LIVE_WAIT` / `LIVE_UNKNOWN`인 기존 LIVE·UPBIT 주문만 조회합니다. 저장된 UUID 또는 `recommendation-{id}`로 Upbit GET을 수행하고 OrderLog와 추천 상태를 동기화합니다. 새 주문, 재주문, 주문 취소, AI 호출, Telegram 승인 생성은 하지 않습니다.

LIVE 주문의 `OrderLog.amount_krw`, `quantity`, `price`는 주문 요청/승인 값이며 실제 체결값으로 덮어쓰지 않습니다. 실제 Upbit 체결 결과는 `executed_quantity`, `executed_funds_krw`, `average_execution_price`, `paid_fee`, `remaining_quantity`, `trades_count`, `execution_synced_at`에 별도로 저장됩니다. `execution_synced_at`은 거래소 체결시각이 아니라 DB 동기화 시각입니다. `trades[]`는 `OrderFill`로 정규화하며 `(order_log_id, exchange_trade_id)`로 중복을 방지하고, 원본 `raw_response`는 audit 용도로 그대로 보존합니다.

저장된 과거 `raw_response.order_status_response`만 사용하는 network-free backfill은 기본 dry-run입니다. 실제 반영은 명시적인 `--apply`가 필요하며 Production 적용은 별도 운영 승인 후 수행합니다.

```bash
python -m scripts.backfill_live_execution_ledger
python -m scripts.backfill_live_execution_ledger --apply
```

### Portfolio valuation snapshot

AI 분석 pipeline은 계좌 스냅샷과 Market Universe 구축 후, AI 추천 전에 `PortfolioSnapshot`과 보유자산별 `PortfolioPositionSnapshot`을 저장합니다. 이 단계는 동일 `pipeline_run_id`에 저장된 `AccountSnapshot`, `MarketUniverseCandidate`, `MarketSnapshot`을 우선 사용합니다. 새 계좌 수집 run type은 `ACCOUNT_SNAPSHOT`이며 과거 `MANUAL` 계좌 run도 reader에서 계속 지원합니다.

`known_total_value_krw`는 현금과 가격을 확보한 position만 합친 알려진 범위의 가치입니다. 모든 양수 보유자산 가격을 확보한 `COMPLETE`에서만 `total_value_krw`가 저장되며, `PARTIAL`에서는 누락 자산을 0원으로 간주하지 않고 `total_value_krw=NULL`로 둡니다. 가격 평가 가능 여부와 cost basis/PnL 계산 가능 여부는 별개입니다. 현금-only portfolio의 position 원가와 미실현손익은 0이고 percentage는 NULL입니다.

Portfolio snapshot은 실제 Upbit 계좌 전체의 현재 평가로 수동 거래·입출금·봇 외 거래 영향도 포함할 수 있습니다. `OrderFill`은 봇이 생성한 LIVE 주문의 체결 ledger이므로 Portfolio valuation을 봇 성과로 해석하면 안 됩니다.

거래 eligibility와 valuation eligibility는 분리됩니다. 신규 BUY 금지 또는 거래 경고
자산도 같은 pipeline의 실제 KRW ticker가 유효하면 평가할 수 있습니다. ticker가 없거나
유효하지 않으면 `PORTFOLIO_COINGECKO_ASSET_MAPPING`에 명시한 asset identity에 한해서만
CoinGecko KRW 현재가를 fallback으로 사용합니다. 예를 들어 운영에서
`APENFT=apenft,QI=qiswap`처럼 설정할 수 있으며, symbol만으로 ID를 추론하지 않습니다.
매핑이 없거나 CoinGecko 가격이 누락·비정상이거나 요청이 실패하면 기존처럼 `PARTIAL`이며
0원으로 추정하지 않습니다. 유효한 same-pipeline Upbit 가격이 있으면 CoinGecko보다 항상
우선하며 해당 asset은 외부 가격 요청 대상에서도 제외됩니다.

`PORTFOLIO_EXCLUDED_ASSETS`는 계좌에는 남아 있지만 운용·NAV 평가 대상이 아닌 자산을
명시하는 별도 정책입니다. 기본값은 비어 있으며, 공백·대소문자·중복을 정규화합니다.
제외자산의 `AccountSnapshot`과 `PortfolioPositionSnapshot`은 삭제하지 않고 position을
`EXCLUDED`/`POLICY_EXCLUDED`로 기록합니다. 다만 NAV, unpriced count, aggregate cost basis와
미실현손익 completeness에서는 제외하고 CoinGecko에도 요청하지 않습니다. 우선순위는
`EXCLUDED → same-pipeline Upbit → explicit CoinGecko mapping → UNPRICED`입니다.
`EXCLUDED`는 가격을 모르는 `UNPRICED`와 다르며, 입출금이나 투자손실로 취급하지 않습니다.
`position_count`는 audit를 위해 제외된 양수 보유자산 position도 포함합니다.

### Portfolio Performance accounting

Portfolio Performance는 계좌 전체 `COMPLETE` NAV에서 완료된 외부 입출금만 제거하는
derived accounting이며 Bot Trading PnL과 별개입니다. `ORDER`는 계좌 내부 교환이라
cash-flow가 아니고, deposit `ACCEPTED`와 withdrawal `DONE`만 반영합니다. KRW는 원금액,
crypto는 Upbit `done_at`으로 저장된 `completed_at` 전에 완전히 종료된 가장 가까운 1분봉
종가로 KRW 평가합니다. `occurred_at`은 요청 생성 시각일 뿐 성과 cash-flow 시각으로
사용하지 않습니다. 완료 상태인데 `completed_at`이 없으면 `PARTIAL`입니다.
현재 가격이나 미래 candle로 보충하지 않으며 가격·activity coverage가 불완전하면 period는
`PARTIAL`입니다.

중간 NAV가 없는 현재 snapshot 주기에는 strict TWR가 불가능하므로 Modified Dietz를
사용합니다. `r=(V_end-V_start-ΣC)/(V_start+Σ(w*C))`, `w`는 cash-flow 시점부터 period
종료까지 남은 시간 비율입니다. 100에서 시작하는 cash-flow-neutral performance index를
연결해 cumulative return, high-water mark, drawdown, MDD를 계산합니다. 불완전 period를
0%로 가정하지 않고 cumulative chain을 끊습니다. 이후 처음 나타나는 `COMPLETE` NAV는
index 100의 새 baseline이 되며, 그 다음 연속 `COMPLETE` period부터 수익률 계산을 재개합니다.
각 Portfolio snapshot은 비어 있지 않은 exclusion 정책의 정규화된 signature를 저장합니다.
연속 COMPLETE snapshot의 signature가 바뀌면 정책상 빠진 가치를 손실로 계산하지 않고
`REBASELINE_AFTER_VALUATION_POLICY_CHANGE` baseline을 생성합니다. 기존 빈 정책과 과거
snapshot의 NULL signature는 같은 정책으로 취급됩니다.
`high_water_mark_krw`와 `drawdown_krw`도 최초 COMPLETE NAV에 performance index를
적용한 cash-flow-neutral KRW-equivalent이며 raw NAV 최고값이 아닙니다.

```bash
python -m scripts.rebuild_portfolio_performance
python -m scripts.rebuild_portfolio_performance --apply
```

기본 명령은 DB write 없는 dry-run입니다. Worker는
`PORTFOLIO_PERFORMANCE_ENABLED=false`가 기본이고, 활성화해도 DB와 public Upbit candle
GET만 사용하며 주문·OpenAI·Telegram을 호출하지 않습니다.

### Bot Trading PnL accounting

Bot Trading PnL은 계좌 전체 Portfolio와 분리된 derived accounting입니다. `LIVE_DONE`과 실제 부분 체결 후 취소된 `LIVE_EXECUTED_CANCELLED` 중 유효한 `executed_quantity`, `executed_funds_krw`, `paid_fee`만 사용합니다. 요청 `amount_krw`/`quantity`/`price`, 추천 금액, ticker, Portfolio 평가는 원가나 실현손익에 사용하지 않습니다.

봇 BUY 수수료는 BOT FIFO lot 원가에 포함하고, 봇 SELL 수수료는 매도대금에서 차감합니다. BOT BUY lineage로 원가를 확인할 수 있는 SELL 수량만 recognized realized PnL에 포함합니다. 기존·수동 보유분을 봇이 SELL한 경우는 `UNMATCHED`, 일부만 연결되면 `PARTIALLY_MATCHED`이며 알 수 없는 원가를 0으로 추정하지 않습니다. 이 FIFO는 fungible coin의 실물 식별이 아니라 bot execution 사이의 명시적인 accounting attribution policy입니다.

기본 rebuild 명령은 read-only dry-run이고 `--apply`만 derived table을 transaction 안에서 교체합니다. 둘 다 Upbit/OpenAI/Telegram/network를 호출하지 않습니다.

```bash
python -m scripts.rebuild_bot_trading_pnl
python -m scripts.rebuild_bot_trading_pnl --apply
```

자동 worker는 `BOT_TRADING_PNL_ENABLED=false`가 기본이며, 활성화하면 기본 300초 간격으로 source signature가 변한 scope만 재계산합니다. 별도 PostgreSQL advisory lock을 사용하며 거래/reconciliation transaction과 결합되지 않습니다.

기본 설정은 `LIVE_ORDER_RECONCILIATION_ENABLED=true`, interval 60초, batch 20개입니다. `ORDER_EXECUTION_MODE`가 LIVE가 아니거나 enabled=false이면 조회 없이 대기합니다. 신규 주문용 LIVE 활성화 플래그를 끄더라도 모드가 LIVE인 동안 기존 주문의 상태 추적은 계속 가능합니다.

```bash
python -m scripts.run_live_order_reconciliation_worker --once
docker compose logs --tail=100 live-order-reconciliation-worker
```

`--once`도 LIVE 환경에서는 실제 private GET을 수행하므로 테스트에서는 fake client를 사용합니다. 수동 `python -m scripts.check_live_order_status --recommendation-id <id>`도 그대로 지원합니다. 상세 상태 매핑과 운영 한계는 [Production LIVE 문서](docs/PRODUCTION_LIVE.md)를 참고하세요.

## 자주 쓰는 명령어

### PostgreSQL만 로컬 포트로 공개해서 실행

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d postgres
```

### 전체 런타임 실행

```powershell
docker compose up -d --build
```

### AI 분석 스케줄러 명시 실행

```powershell
docker compose --profile scheduler up -d --build ai-trade-scheduler
```

### AI 분석 스케줄러 로그 확인

```powershell
docker compose --profile scheduler logs --tail=100 ai-trade-scheduler
```

### AI 분석 파이프라인 1회 실행

```powershell
docker compose --profile manual run --rm ai-trade-analysis
```

### 전체 상태 확인

```powershell
docker compose ps -a
```

### Operational Alerting

Operational Alerting은 거래 엔진이 아니라 기존 pipeline 실패와 장기 미확정 LIVE
주문을 운영자에게 알리는 별도 계층입니다. 주문 생성·취소·재주문, reconciliation,
AI 판단을 수행하거나 변경하지 않습니다.

Pipeline child는 raw exception 대신 분류된 category와 고정 안전 문구만 임시 JSON으로
부모에게 전달합니다. 부모는 `OperationalAlert` outbox를 먼저 저장한 뒤 즉시 Telegram
발송을 시도하며, 일시 실패는 같은 alert row를 재시도합니다. Background worker는
`LIVE_PLACED`, `LIVE_WAIT`, `LIVE_UNKNOWN`인 LIVE·UPBIT 주문을 `created_at` 기준으로
감지해 주문당 한 번만 경고합니다.

배포 기본값은 `OPERATIONAL_ALERTING_ENABLED=false`입니다. 이 flag는 stale 감지와
outbox retry worker만 제어하며 기존 pipeline 실패의 즉시 알림 시도는 계속 유지됩니다.
읽기 전용 사전 점검은 다음 명령을 사용합니다.

```powershell
python -m scripts.check_operational_alerts
```

이 진단은 DB SELECT만 수행하며 Telegram, Upbit, OpenAI를 호출하지 않습니다.

### Upbit Order Chance LIVE preflight

`LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED=false`가 기본이므로 배포만으로 기존 LIVE 주문
경로는 바뀌지 않습니다. 활성화하면 Telegram 승인 이후 기존 idempotency와 identifier
recovery를 먼저 수행하고, 새 주문 POST 직전에 Upbit `GET /v1/orders/chance`로 현재
주문 유형, 최소·최대 금액, 주문 가능 잔고와 수수료율을 검증합니다.

BUY 승인금액과 SELL 승인수량은 chance 결과에 맞춰 자동 축소하지 않습니다. 조건 미충족,
응답 누락 또는 chance GET 실패는 `LIVE_FAILED`로 기록하고 주문을 생성하지 않습니다.
실제 POST 이후 응답이 불명확한 경우에만 기존 `LIVE_UNKNOWN`/identifier recovery가
그대로 적용됩니다. 추천 단계의 5,000원 상수는 네트워크 없는 conservative pre-screen이고,
preflight 활성화 시 실제 실행의 current canonical rule은 chance 응답입니다.

다음 진단은 인증된 `GET /v1/orders/chance` 한 번만 수행하며 주문/test 주문, DB,
Telegram, OpenAI를 사용하지 않습니다.

```powershell
python -m scripts.check_upbit_order_chance --market KRW-BTC
```

### Account Activity Ledger

`AccountActivity`는 Upbit의 종료 주문, 입금, 출금 이력을 정규화해 저장하는 read-only
source ledger입니다. 수동 BUY/SELL은 계좌 안의 자산 교환이므로 외부 cash-flow가 아니며,
입금은 `IN`, 출금은 `OUT`으로만 표시합니다. 처리 중이거나 실패한 입출금을 성과상 완료
cash-flow로 인정하는 계산은 이번 기능에 없습니다. Crypto 입출금도 현재 가격으로 소급해
KRW 가치를 만들지 않습니다.

기본 명령은 read-only API와 DB SELECT만 수행하는 dry-run입니다. DB 반영은 명시적인
`--apply`에서만 수행합니다. 초기 기준 데이터가 없으면 timezone이 포함된 `--start-at`을
지정해야 합니다.

```powershell
python -m scripts.check_upbit_account_activity_access
python -m scripts.sync_account_activities --start-at 2026-08-01T00:00:00+09:00
python -m scripts.sync_account_activities --start-at 2026-08-01T00:00:00+09:00 --apply
```

`ACCOUNT_ACTIVITY_SYNC_ENABLED=false`가 기본입니다. Ledger는 기존 OrderLog,
Execution Ledger, Bot FIFO/PnL, Portfolio snapshot을 수정하지 않습니다. Bot 외부 주문과
입출금을 수집하지만 external depletion attribution은 후속 기능입니다. Portfolio
Performance는 완료 상태와 event-time 가격을 별도 derived table에서 해석합니다.

### 서버 런타임 안전 점검

```powershell
bash scripts/check_server_runtime_safety.sh
bash scripts/check_server_runtime_safety.sh --strict-live
bash scripts/check_server_runtime_safety.sh --production-live
```

### Docker Compose 설정 확인

```powershell
docker compose --profile manual --profile scheduler config --quiet
```

### 전체 종료

```powershell
docker compose down
```

### 테스트

```powershell
uv run pytest -q
```

### 포맷/린트 확인

```powershell
uv run ruff format --check .
uv run ruff check .
```
