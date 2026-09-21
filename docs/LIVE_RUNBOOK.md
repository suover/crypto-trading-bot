# 첫 5,000 KRW 제한적 LIVE 주문 런북

이 런북의 Compose 및 `scripts/*.sh` 명령은 repository root에서 실행합니다.
Python 소스와 Dockerfile은 `trading-engine/`에 있지만 컨테이너 내부 `/app` 및
`python -m scripts.…` 명령은 동일합니다. `.env`와 `.secrets/`도 root에 유지합니다.

이 절차는 수동 승인된 `KRW-BTC` BUY 1건만 검증하기 위한 것입니다. 예약 LIVE 거래는 이 기능에서 활성화하지 않습니다.

## Phase 1: 안전 준비

서버를 다음 안전 상태로 유지합니다.

```env
ORDER_EXECUTION_MODE=MOCK
LIVE_ORDER_ENABLED=false
LIVE_ORDER_CONFIRMATION=
MARKET_UNIVERSE_MODE=STATIC
LIVE_DYNAMIC_MARKET_ENABLED=false
MAX_ORDER_AMOUNT_KRW=5000
DAILY_MAX_ORDER_AMOUNT_KRW=5000
ALLOWED_MARKETS=KRW-BTC
AI_ANALYSIS_SCHEDULER_ENABLED=false
```

진행 전에 다음을 모두 확인합니다.

- DB 백업과 `.env`, `/etc/crypto-trading-bot/secrets`의 안전한 백업이 존재합니다.
- `/etc/crypto-trading-bot/secrets`와 필수 비밀 파일이 모두 존재합니다.
- 비밀 디렉터리 권한은 `700`, 각 비밀 파일 권한은 `600`입니다.
- `.env`에 올바른 `SECRET_DIR`이 설정되어 있고 `.env` 권한은 `600`입니다.
- AI 스케줄러가 중지되어 있고 시작되지 않습니다.
- Telegram listener가 실행 중입니다.
- Upbit 키에 주문 및 주문 조회 권한이 있습니다.
- Upbit 키에 출금 권한이 없습니다.
- 서버 IP가 Upbit 허용 IP에 정확히 등록되어 있습니다.

비밀 파일 준비 절차는 [서버 운영 가이드](OPERATIONS.md)를 따릅니다.

## Phase 2: 공식 안전 주문 테스트

```bash
docker compose --profile manual run --rm ai-trade-analysis \
  python -m scripts.check_upbit_order_test \
  --market KRW-BTC \
  --amount-krw 5000
```

이 명령은 Upbit `/v1/orders/test`만 호출합니다. 실제 주문과 실제 수수료는 발생하지 않으며, recommendation·approval request·`order_logs`를 변경하지 않습니다. 매 실행마다 만든 테스트 identifier는 실제 주문 identifier로 재사용하지 않습니다. 실패하면 LIVE 테스트를 중단합니다.

## Phase 3: 제한된 LIVE 주문 1건

공식 주문 테스트 성공 후에만 다음 값으로 변경합니다.

```env
ORDER_EXECUTION_MODE=LIVE
LIVE_ORDER_ENABLED=true
LIVE_ORDER_CONFIRMATION=ENABLE_LIVE_UPBIT_ORDERS
MARKET_UNIVERSE_MODE=STATIC
LIVE_DYNAMIC_MARKET_ENABLED=false
MAX_ORDER_AMOUNT_KRW=5000
DAILY_MAX_ORDER_AMOUNT_KRW=5000
ALLOWED_MARKETS=KRW-BTC
AI_ANALYSIS_SCHEDULER_ENABLED=false
```

필요한 장기 실행 서비스만 재시작하며 스케줄러는 시작하지 않습니다. 이어서 엄격 점검을 실행합니다.

```bash
bash scripts/check_server_runtime_safety.sh --strict-live
docker compose --profile manual run --rm ai-trade-analysis \
  python -m scripts.check_live_trading_readiness --strict
```

점검이 모두 성공한 뒤 수동 AI 분석을 한 번만 실행합니다. 만료되지 않은 `KRW-BTC` BUY 추천 중 정확히 5,000 KRW인 한 건만 Telegram에서 승인합니다. 다른 마켓·액션·금액은 승인하지 않습니다.

## Phase 4: 주문 재조정

```bash
docker compose --profile manual run --rm ai-trade-analysis \
  python -m scripts.check_live_order_status \
  --recommendation-id <ID>
```

Telegram 결과, Upbit 앱 주문 내역, 로컬 `order_logs`, recommendation 상태, exchange UUID, 체결 수량과 지급 수수료를 서로 대조합니다. `LIVE_UNKNOWN`이면 동일 추천을 다시 승인하거나 수동 재주문하지 말고 이 조회와 Upbit 주문 내역 확인을 먼저 수행합니다.

## Phase 5: 즉시 롤백

한 건의 확인이 끝나거나 이상이 감지되면 즉시 다음 상태로 되돌립니다.

```env
ORDER_EXECUTION_MODE=MOCK
LIVE_ORDER_ENABLED=false
LIVE_ORDER_CONFIRMATION=
AI_ANALYSIS_SCHEDULER_ENABLED=false
```

영향받는 장기 실행 서비스만 재시작하고 스케줄러는 계속 중지합니다. 이 기능으로 예약 LIVE 거래를 활성화하지 않습니다.
