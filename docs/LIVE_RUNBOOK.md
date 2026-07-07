# 제한적 라이브 테스트 런북

이 문서는 첫 제한적 라이브 주문 테스트를 위한 운영 절차입니다. 투자 조언이 아니며, 이 프로젝트는 수익을 보장하지 않습니다. 라이브 거래는 실제 손실을 만들 수 있으므로 작은 금액으로 수동 확인 절차를 거쳐야 합니다.

## 현재 권장 라이브 테스트 정책

- 수동 AI 분석만 사용합니다.
- Telegram 승인이 반드시 필요합니다.
- 스케줄러는 OFF 상태로 유지합니다.
- 첫 테스트는 1회 5,000 KRW로 제한합니다.
- 허용 마켓은 `ALLOWED_MARKETS=KRW-BTC`만 사용합니다.

## 사전 체크리스트

라이브 테스트 전에 다음 항목을 모두 확인합니다.

- DB 백업이 완료되었습니다.
- `telegram-listener`가 실행 중입니다.
- `mock-order-retry-worker`가 실행 중입니다.
- `ai-trade-scheduler`가 중지되어 있습니다.
- Upbit API 키에 출금 권한이 없습니다.
- Upbit API 허용 IP가 서버 IP로 올바르게 설정되어 있습니다.
- 서버 `.env` 권한이 `600`입니다.
- 백업 파일 권한이 `600`입니다.
- 서버 런타임 안전 점검이 `--strict-live` 모드로 통과했습니다.
- Python 라이브 준비 상태 점검이 strict 모드로 통과했습니다.

권한 확인 예시:

```bash
ls -l .env
ls -l ${HOME}/backups/crypto_trading_bot_*.sql
```

## 제한적 라이브 테스트용 `.env` 값

첫 제한적 라이브 테스트에서는 다음 값을 사용합니다.

```env
ORDER_EXECUTION_MODE=LIVE
LIVE_ORDER_ENABLED=true
LIVE_ORDER_CONFIRMATION=ENABLE_LIVE_UPBIT_ORDERS
MAX_ORDER_AMOUNT_KRW=5000
DAILY_MAX_ORDER_AMOUNT_KRW=5000
ALLOWED_MARKETS=KRW-BTC
AI_ANALYSIS_SCHEDULER_ENABLED=false
```

실제 API 키와 토큰은 문서에 기록하지 않습니다. 서버 `.env`에서만 관리합니다.

## 서버 런타임 안전 점검

Python 라이브 준비 상태 점검 전에 서버 런타임 구성이 안전한지 확인합니다.

```bash
bash scripts/check_server_runtime_safety.sh --strict-live
```

이 스크립트는 컨테이너를 변경하지 않고 주문도 실행하지 않습니다.

## 라이브 준비 상태 확인

서버 런타임 안전 점검을 통과한 뒤 Python 레벨 라이브 준비 상태를 엄격 모드로 확인합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis python -m scripts.check_live_trading_readiness --strict
```

실패 항목이 있으면 라이브 테스트를 진행하지 않습니다.

## 수동 AI 분석 실행

스케줄러를 사용하지 않고 수동으로 1회 분석합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis
```

## Telegram 승인 규칙

Telegram 승인 요청을 받은 뒤 다음 기준을 확인합니다.

- 마켓이 예상한 `KRW-BTC`인지 확인합니다.
- 주문 금액이 예상한 5,000 KRW 범위인지 확인합니다.
- 액션이 예상과 다르면 승인하지 않습니다.
- 마켓이나 금액이 예상과 다르면 승인하지 않습니다.
- 조금이라도 이상하면 거절합니다.

## 승인 후 확인

승인 후에는 다음을 확인합니다.

- Telegram 결과 메시지를 확인합니다.
- Upbit 앱 또는 주문 내역에서 실제 주문 결과를 확인합니다.
- 애플리케이션 로그를 확인합니다.

로그 확인 예시:

```bash
docker compose logs --tail=100 telegram-listener
docker compose logs --tail=100 mock-order-retry-worker
```

## 즉시 중지 또는 롤백

이상이 있거나 라이브 테스트를 종료하려면 서버 `.env`를 안전 값으로 되돌립니다.

```env
ORDER_EXECUTION_MODE=MOCK
LIVE_ORDER_ENABLED=false
LIVE_ORDER_CONFIRMATION=
```

변경 후 listener와 worker를 재시작합니다.

```bash
docker compose up -d --build telegram-listener mock-order-retry-worker
```

스케줄러가 실행 중이면 중지합니다.

```bash
docker compose --profile scheduler stop ai-trade-scheduler
```

## 라이브 테스트 후 체크리스트

- DB 주문 로그를 확인합니다.
- Upbit 실제 주문 결과와 DB 기록이 맞는지 확인합니다.
- 테스트 후 DB 백업을 새로 생성합니다.
- 실행 시각, 설정값, Telegram 승인 내용, 실제 주문 결과를 수동으로 기록합니다.

테스트 결과가 예상과 다르면 추가 라이브 테스트를 진행하지 말고 원인을 먼저 확인합니다.