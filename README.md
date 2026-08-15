# crypto-trading-bot

시장 데이터, 계좌 상태, 설정값을 바탕으로 AI 매매 후보를 만들고, 사용자가 Telegram에서 승인한 경우에만 주문 흐름을 진행하는 승인형 코인 트레이딩 봇 프로젝트입니다.

현재는 실전 자동매매보다 **데이터 수집 → AI 추천 생성 → Telegram 승인 요청 → 모의 주문 처리** 흐름을 안정적으로 구성하는 데 초점을 둡니다.

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
| `ai-trade-analysis`       | 수동 실행      | 시장/계좌/캔들 수집, AI 추천 생성, Telegram 알림 발송 |

기본 서버/프로덕션 유사 런타임 명령인 `docker compose up -d --build`는 `postgres`, `migrate`, `telegram-listener`, `mock-order-retry-worker`만 실행합니다. `ai-trade-scheduler`는 기본 런타임에 포함되지 않으며, `scheduler` profile을 명시할 때만 실행됩니다.

`ai-trade-analysis`는 `manual` profile에 포함된 수동 실행 서비스입니다. 즉시 1회 분석을 테스트할 때 사용합니다.

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

1. 시장 현재가 스냅샷 수집
2. 계좌 잔고 스냅샷 수집
3. 시장 캔들 수집
4. AI 매매 추천 생성
5. AI 추천 결과 Telegram 알림 및 승인 요청 발송

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

## AI 분석 파이프라인 수동 실행

Docker Compose 런타임이 준비된 상태에서 AI 분석 파이프라인을 1회 실행할 수 있습니다.

```powershell
docker compose --profile manual run --rm ai-trade-analysis
```

이 명령은 다음 단계를 순서대로 실행합니다.

1. 시장 현재가 스냅샷 수집
2. 계좌 잔고 스냅샷 수집
3. 시장 캔들 수집
4. AI 매매 추천 생성
5. AI 추천 결과 Telegram 알림 및 승인 요청 발송

`BUY` 또는 `SELL` 추천이 생성되면 Telegram 승인 요청이 발송됩니다. 사용자가 승인해야 후속 주문 흐름으로 넘어갑니다.

## 중복 실행 방지

`ai-trade-scheduler`, `telegram-listener`, `mock-order-retry-worker`는 PostgreSQL advisory lock을 사용해 중복 실행을 방지합니다.

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

현재 단계에서는 실제 거래소 주문 실행보다 승인/재시도/알림 흐름 검증에 초점을 둡니다.

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

### 서버 런타임 안전 점검

```powershell
bash scripts/check_server_runtime_safety.sh
bash scripts/check_server_runtime_safety.sh --strict-live
```

### Docker Compose 설정 확인

```powershell
docker compose --profile manual config > $null
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
