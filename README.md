# crypto-trading-bot

시장 데이터, 계좌 상태, 설정값을 바탕으로 AI 매매 후보를 만들고, 사용자가 Telegram에서 승인한 경우에만 주문 흐름을 진행하는 승인형 코인 트레이딩 봇 프로젝트입니다.

현재는 실전 자동매매보다 **데이터 수집 → AI 추천 생성 → Telegram 승인 요청 → 모의 주문 처리** 흐름을 안정적으로 구성하는 데 초점을 둡니다.

## 주요 구성

- Python 3.14
- uv
- PostgreSQL 17
- Docker Compose
- SQLAlchemy / Alembic
- Telegram Bot 연동
- OpenAI API 연동
- 거래소 API 연동 준비
- Ruff / Pytest 기반 품질 검증

## 실행 서비스

Docker Compose 기준으로 다음 서비스가 실행됩니다.

| 서비스 | 실행 방식 | 역할 |
| --- | --- | --- |
| `postgres` | 상시 실행 | 로컬 PostgreSQL 17 데이터베이스 |
| `migrate` | 1회 실행 후 종료 | Alembic DB 마이그레이션 실행 |
| `telegram-listener` | 상시 실행 | Telegram 승인/거절 버튼 콜백 수신 |
| `mock-order-retry-worker` | 상시 실행 | 실패한 모의 주문 및 알림 재시도 처리 |
| `ai-trade-analysis` | 수동 실행 | 시장/계좌/캔들 수집, AI 추천 생성, Telegram 알림 발송 |

`ai-trade-analysis`는 `manual` profile에 포함된 수동 실행 서비스입니다. 기본 런타임 실행 명령인 `docker compose up -d --build`만으로는 자동 실행되지 않습니다.

## 사전 준비

다음 도구가 설치되어 있어야 합니다.

- Python 3.14
- uv
- Docker Desktop
- Git

PostgreSQL은 별도로 로컬에 설치하지 않아도 됩니다. 개발용 PostgreSQL은 Docker Compose의 `postgres` 서비스로 실행합니다.

## 환경 변수 설정

예시 환경 변수 파일을 복사해서 `.env`를 만듭니다.

```powershell
Copy-Item .env.example .env
```

`.env` 파일에 필요한 값을 채웁니다.

```env
APP_ENV=local

DATABASE_URL=postgresql+psycopg://trading_user:trading_pass@localhost:5432/crypto_trading_bot

OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

UPBIT_ACCESS_KEY=
UPBIT_SECRET_KEY=

TRADING_MODE=AI_APPROVAL
MAX_ORDER_AMOUNT_KRW=10000
DAILY_MAX_ORDER_AMOUNT_KRW=30000

ALLOWED_MARKETS=KRW-BTC,KRW-ETH
```

비밀값은 로컬 `.env`에서만 관리합니다. `.env.example`에는 실제 API 키, Telegram 토큰, 거래소 키를 넣지 않습니다.

## 로컬 개발 실행

로컬 개발에서는 PostgreSQL만 Docker로 실행하고, Python 명령은 호스트 환경에서 실행할 수 있습니다.

### 1. PostgreSQL 실행

```powershell
docker compose up -d postgres
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

## Docker Compose 전체 런타임 실행

전체 런타임을 Docker Compose로 실행합니다.

```powershell
docker compose up -d --build
```

이 명령은 다음 흐름으로 실행됩니다.

1. `postgres` 컨테이너 실행
2. PostgreSQL healthcheck 통과 대기
3. `migrate` 컨테이너에서 Alembic 마이그레이션 실행
4. 마이그레이션 성공 후 `telegram-listener` 실행
5. 마이그레이션 성공 후 `mock-order-retry-worker` 실행

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

`telegram-listener`와 `mock-order-retry-worker`는 PostgreSQL advisory lock을 사용해 중복 실행을 방지합니다.

이미 같은 프로세스가 실행 중이면 추가 실행된 프로세스는 바로 종료됩니다.

예상 메시지 예시:

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

로컬 DB 데이터까지 초기화해야 할 때만 다음 명령을 사용합니다.

```powershell
docker compose down -v
```

## 모의 주문 재시도 워커

`mock-order-retry-worker`는 실패한 모의 주문 처리와 Telegram 알림 재시도 흐름을 담당합니다.

현재 단계에서는 실제 거래소 주문 실행보다 승인/재시도/알림 흐름 검증에 초점을 둡니다.

## 자주 쓰는 명령어

### PostgreSQL만 실행

```powershell
docker compose up -d postgres
```

### 전체 런타임 실행

```powershell
docker compose up -d --build
```

### AI 분석 파이프라인 1회 실행

```powershell
docker compose --profile manual run --rm ai-trade-analysis
```

### 전체 상태 확인

```powershell
docker compose ps -a
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