# crypto-trading-bot

AI가 시장 데이터와 설정값을 바탕으로 매매 후보를 만들고, 사용자가 Telegram에서 승인한 경우에만 주문 흐름을 진행하는 승인형 코인 트레이딩 봇 프로젝트입니다.

현재 단계는 실제 자동매매보다 **안전한 승인형/모의 주문 기반 런타임 구성**에 초점을 둡니다. Docker Compose로 PostgreSQL, DB 마이그레이션, Telegram 승인 리스너, 모의 주문 재시도 워커를 함께 실행할 수 있습니다.

## 주요 구성

- Python 3.14
- uv
- PostgreSQL 17
- Docker Compose
- SQLAlchemy / Alembic
- Telegram Bot 연동
- OpenAI API 연동
- Upbit API 연동 준비
- Ruff / Pytest 기반 품질 검증

## 실행 서비스

Docker Compose 기준으로 다음 서비스가 실행됩니다.

| 서비스 | 역할 |
| --- | --- |
| `postgres` | 로컬 PostgreSQL 17 데이터베이스 |
| `migrate` | Alembic DB 마이그레이션 실행 |
| `telegram-listener` | Telegram 승인/거절 버튼 콜백 수신 |
| `mock-order-retry-worker` | 실패한 모의 주문 및 알림 재시도 처리 |

`migrate` 서비스는 마이그레이션을 완료하면 정상 종료됩니다. `telegram-listener`와 `mock-order-retry-worker`는 백그라운드에서 계속 실행됩니다.

## 사전 준비

다음 도구가 설치되어 있어야 합니다.

- Python 3.14
- uv
- Docker Desktop
- Git

PostgreSQL은 별도로 로컬에 설치하지 않아도 됩니다. 개발용 PostgreSQL은 Docker Compose의 `postgres` 서비스로 실행할 수 있습니다.

## 환경 변수 설정

먼저 예시 환경 변수 파일을 복사해서 `.env`를 만듭니다.

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

주의사항:

- `.env`는 절대 커밋하지 않습니다.
- 실제 API 키, Telegram 토큰, Upbit 키는 GitHub에 올리지 않습니다.
- `.env.example`에는 실제 비밀값을 넣지 않습니다.

## 로컬 개발 실행

로컬 개발에서는 PostgreSQL만 Docker로 띄우고, Python 명령은 호스트 환경에서 실행할 수 있습니다.

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

전체 런타임을 Docker Compose로 실행하려면 다음 명령을 사용합니다.

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

실행 중인 전체 로그를 따라가려면 다음 명령을 사용할 수 있습니다.

```powershell
docker compose logs -f
```

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

## 데이터 삭제 주의

다음 명령은 PostgreSQL 볼륨까지 삭제합니다.

```powershell
docker compose down -v
```

`docker compose down -v`를 실행하면 로컬 DB 데이터가 사라질 수 있으므로, 테스트 데이터를 완전히 초기화하려는 경우가 아니라면 사용하지 않습니다.

## 모의 주문 재시도 워커 주의사항

`mock-order-retry-worker`는 이름 그대로 모의 주문 재시도 흐름을 처리합니다.

현재 이 워커는 실제 Upbit 주문을 실행하지 않습니다. 실패한 모의 주문과 Telegram 알림 재시도 흐름을 검증하기 위한 런타임 구성입니다.

실제 주문 기능은 별도의 안전장치, 주문 한도, 승인 절차, 로그 검증이 충분히 준비된 뒤에만 확장합니다.

## 자주 쓰는 명령어

### PostgreSQL만 실행

```powershell
docker compose up -d postgres
```

### 전체 런타임 실행

```powershell
docker compose up -d --build
```

### 전체 상태 확인

```powershell
docker compose ps -a
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

## 개발 원칙

- 실제 API 키는 커밋하지 않습니다.
- 실제 주문 실행 기능은 마지막 단계에서만 다룹니다.
- Telegram 승인 기반 흐름을 우선 검증합니다.
- Docker Compose 런타임과 로컬 개발 흐름을 함께 유지합니다.
- 변경 후에는 `ruff`, `pytest`, Docker Compose 설정 확인을 수행합니다.
