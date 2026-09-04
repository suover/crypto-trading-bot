# 운영 가이드

이 문서는 서버에서 `crypto-trading-bot`을 반복 가능하고 안전하게 운영하기 위한 절차를 정리합니다. 서버 기준 프로젝트 경로는 다음과 같습니다.

```bash
/home/ubuntu/apps/crypto-trading-bot
```

현재 scheduled approval-based Production LIVE 운영과 GitHub Actions CD 절차는 [Production LIVE 운영 문서](PRODUCTION_LIVE.md)를 우선 참고합니다. [제한적 LIVE 런북](LIVE_RUNBOOK.md)은 첫 5,000 KRW 검증 절차로 계속 보존됩니다.

## 서버 프로젝트 경로로 이동

```bash
cd /home/ubuntu/apps/crypto-trading-bot
```

## Git 상태 확인

운영 작업 전에는 현재 변경 사항을 먼저 확인합니다.

```bash
git status --short
```

의도하지 않은 변경 사항이 있으면 배포나 재시작 전에 내용을 확인합니다.

## 최신 main 가져오기

현재 브랜치와 원격 상태를 확인한 뒤 main을 최신화합니다.

```bash
git branch --show-current
git fetch origin main
git merge --ff-only origin/main
```

운영 서버에서 직접 수정한 파일이 있다면 `git pull` 전에 반드시 내용을 확인합니다. 특히 `.env`는 Git에 포함하지 않습니다.

## 서버 비밀 파일 준비

서버 비밀 파일은 `/etc/crypto-trading-bot/secrets`에 준비합니다.

```bash
sudo mkdir -p /etc/crypto-trading-bot/secrets
sudo chmod 700 /etc/crypto-trading-bot/secrets

sudo touch /etc/crypto-trading-bot/secrets/postgres_password
sudo touch /etc/crypto-trading-bot/secrets/openai_api_key
sudo touch /etc/crypto-trading-bot/secrets/telegram_bot_token
sudo touch /etc/crypto-trading-bot/secrets/upbit_access_key
sudo touch /etc/crypto-trading-bot/secrets/upbit_secret_key

sudo chmod 600 /etc/crypto-trading-bot/secrets/*
```

각 파일에는 비밀값만 저장하며 `KEY=`, 따옴표, 주석을 넣지 않습니다. 비밀 디렉터리는 Git에 포함하지 않습니다. 서버 `.env`에는 다음 경로와 기존 비밀이 아닌 런타임 설정을 유지합니다.

```env
SECRET_DIR=/etc/crypto-trading-bot/secrets
```

컨테이너 시작 전에 필수 비밀 파일이 모두 존재하는지 확인합니다.

> **주의:** 기존 PostgreSQL 데이터 볼륨을 사용한다면 `postgres_password`에는 기존 데이터베이스 역할이 현재 사용하는 암호를 먼저 저장해야 합니다. 파일만 변경해도 기존 역할 암호는 회전되지 않습니다.

## Docker Compose 설정 확인

컨테이너를 재생성하기 전에 Compose 설정을 확인합니다.

```bash
docker compose config --quiet
```

수동 AI 분석 profile까지 포함해 확인하려면 다음 명령을 사용할 수 있습니다.

```bash
docker compose --profile manual config --quiet
```

두 명령은 해석된 환경값을 출력하지 않고 설정의 유효성을 검사합니다.

## 기본 런타임 시작 또는 재생성

서버/프로덕션 유사 기본 런타임은 다음 명령으로 시작하거나 재생성합니다.

```bash
docker compose up -d --build
```

기본 런타임은 다음 서비스를 대상으로 합니다.

- `postgres`
- `migrate`
- `telegram-listener`
- `mock-order-retry-worker`
- `live-order-reconciliation-worker`
- `bot-trading-pnl-worker`

`ai-trade-scheduler`는 기본 런타임에서 시작되지 않습니다.

## 컨테이너 상태 확인

```bash
docker compose ps -a
```

`postgres`, `telegram-listener`, `mock-order-retry-worker`, `live-order-reconciliation-worker`, `bot-trading-pnl-worker`가 실행 중인지 확인합니다. `migrate`는 정상적으로 완료된 뒤 종료될 수 있습니다.

`bot-trading-pnl-worker`도 기본 runtime에 포함되지만 `BOT_TRADING_PNL_ENABLED=false` 기본값에서는 DB accounting query/write 없이 대기합니다.

## 로그 확인

마이그레이션 로그:

```bash
docker compose logs --tail=100 migrate
```

Telegram listener 로그:

```bash
docker compose logs --tail=100 telegram-listener
```

모의 주문 재시도 워커 로그:

```bash
docker compose logs --tail=100 mock-order-retry-worker
```

기존 LIVE 주문 상태 추적 로그:

```bash
docker compose logs --tail=100 live-order-reconciliation-worker
```

Bot PnL accounting 로그:

```bash
docker compose logs --tail=100 bot-trading-pnl-worker
```

LIVE execution ledger에서 `amount_krw`/`quantity`/`price`는 주문 요청값이고, `executed_quantity`/`executed_funds_krw`/`average_execution_price`/`paid_fee`는 Upbit 응답에서 정규화한 실제 체결값입니다. 개별 `trades[]`는 `order_fills`에 저장되고 원본 `raw_response`는 유지됩니다. `execution_synced_at`은 거래소 체결시각이 아니라 마지막 DB 동기화 시각입니다.

과거 저장 JSON을 점검할 때는 먼저 dry-run만 실행합니다. 이 명령은 네트워크를 사용하지 않고 `LIVE`·`UPBIT`의 `raw_response.order_status_response`만 읽습니다. `--apply`는 대상 DB를 재확인한 뒤 별도 승인된 작업에서만 사용합니다.

```bash
python -m scripts.backfill_live_execution_ledger
python -m scripts.backfill_live_execution_ledger --apply
```

## Bot Trading PnL accounting

Portfolio snapshot은 수동 거래와 입출금을 포함할 수 있는 실제 계좌 전체 상태입니다. Bot Trading PnL은 이와 별개로 이 프로그램이 만든 terminal LIVE 주문의 정규화된 체결 summary만 사용합니다. `LIVE_DONE`/`LIVE_EXECUTED_CANCELLED`만 source이고, BUY fee는 FIFO lot 원가에 포함하며 SELL fee는 proceeds에서 차감합니다.

봇 BUY lot이 없는 SELL은 `UNMATCHED`, 일부 수량만 연결되면 `PARTIALLY_MATCHED`입니다. 해당 수량의 원가를 계좌 평단이나 Portfolio에서 추정하지 않습니다. 불완전 terminal source가 발견되면 같은 market의 이후 chronology는 fail-closed로 계산하지 않고 summary를 `PARTIAL`로 둡니다. 다른 market은 계속 계산합니다.

점검은 항상 dry-run부터 시작합니다. 기본 명령은 SELECT와 normalization만 수행하며 INSERT/UPDATE/DELETE/flush/commit/row lock을 하지 않습니다. Production `--apply`는 별도 승인된 운영 작업으로 취급합니다.

```bash
python -m scripts.rebuild_bot_trading_pnl
python -m scripts.rebuild_bot_trading_pnl --apply
python -m scripts.run_bot_trading_pnl_worker --once
```

worker는 DB-only이며 주문 생성/조회/취소, Upbit, AI, Telegram 호출이 없습니다. `BOT_TRADING_PNL_ENABLED=false`가 rollout-safe 기본이고 interval 기본값은 300초입니다.

## Portfolio Performance accounting

계좌 성과는 `COMPLETE PortfolioSnapshot`과 완료된 Account Activity 입출금을 사용합니다.
ORDER는 제외하고, crypto flow는 event 시각 이전에 완전히 종료된 Upbit 1분봉 종가로
평가합니다. 여기서 event 시각은 `created_at/occurred_at`이 아니라 완료 `done_at/completed_at`입니다.
Period inclusion은 `(previous_snapshot, current_snapshot]`입니다. Deposit과 withdrawal coverage 또는 가격이 불완전하면 해당 period와 이후
cumulative chain은 `PARTIAL`입니다.

Modified Dietz period return을 100 기준 performance index로 연결하므로 외부 입출금이
high-water mark/drawdown/MDD를 직접 왜곡하지 않습니다. 먼저 dry-run을 확인하고 운영 반영은
별도 승인과 백업 후에만 `--apply`로 수행합니다.
KRW high-water mark/drawdown은 최초 COMPLETE NAV에 index를 적용한 조정값이며 raw NAV가
아닙니다.

```bash
python -m scripts.rebuild_portfolio_performance
python -m scripts.rebuild_portfolio_performance --apply
docker compose logs --tail=100 portfolio-performance-worker
```

`PORTFOLIO_PERFORMANCE_ENABLED=false`가 배포 기본값입니다. Worker는 PostgreSQL secret만
mount하며 Upbit private key, OpenAI, Telegram secret을 받지 않습니다.

AI 분석 pipeline은 AccountSnapshot → Market Universe → Portfolio valuation → AI recommendation → Telegram 순서로 실행됩니다. Portfolio 단계는 동일 `CRYPTO_TRADING_PIPELINE_RUN_ID`의 DB 가격을 우선합니다. Upbit 가격이 없는 보유자산은 `PORTFOLIO_COINGECKO_ASSET_MAPPING`에 명시적인 `ASSET=coingecko-id`가 있는 경우에만 CoinGecko KRW 현재가를 조회합니다. 기본 매핑은 비어 있고 symbol 자동 추론은 하지 않으며, 누락·비정상·요청 실패는 0원이 아닌 `UNPRICED`로 유지합니다. 새 계좌 수집은 `ACCOUNT_SNAPSHOT` run type을 사용하고 legacy `MANUAL` account run도 조회 호환됩니다.

`PORTFOLIO_EXCLUDED_ASSETS`는 기본값이 빈 explicit policy입니다. 여기에 포함된 currency는
AccountSnapshot과 PortfolioPosition audit row를 유지하되 position을
`EXCLUDED`/`POLICY_EXCLUDED`로 기록하고 NAV, unpriced count, aggregate cost basis/PnL 및
CoinGecko 요청에서 제외합니다. Market Universe의 ranked/static/held 후보와 신규 LIVE 주문도
차단합니다. 이는 cash-flow나 손실이 아니며 Account Activity와 Bot FIFO/PnL을 변경하지
않습니다. exclusion signature가 연속 COMPLETE snapshot 사이에서 달라지면 Performance는
정책 차이를 수익률로 계산하지 않고 새 index-100 baseline을 만듭니다.

`COMPLETE`는 KRW와 정책상 포함된 모든 양수 보유자산의 가격을 확보했다는 뜻입니다. `PARTIAL`의 `known_total_value_krw`는 알려진 범위만 합한 값이며 전체 계좌 총자산이 아닙니다. `total_value_krw`는 PARTIAL에서 NULL입니다. 가격은 모두 있어도 포함자산 cost basis가 하나라도 없으면 valuation은 COMPLETE일 수 있지만 aggregate cost basis와 미실현손익은 NULL입니다.

이 worker는 LIVE·UPBIT 주문의 `LIVE_PLACED` / `LIVE_WAIT` / `LIVE_UNKNOWN` 상태만 Upbit GET으로 조회하고 DB를 동기화합니다. 새 주문 생성, 자동 재주문, 자동 취소, Telegram 승인 생성, AI 호출은 하지 않습니다. API 오류 시 미확정 상태를 유지하고 다음 cycle에서 다시 조회합니다. terminal 주문과 MOCK 주문은 polling하지 않습니다.

`LIVE_ORDER_RECONCILIATION_ENABLED=false` 또는 non-LIVE 모드에서는 조회 없이 대기합니다. Production LIVE 점검은 worker 실행과 활성화를 요구합니다. `--once`는 한 batch만 처리하고 종료하며, 실제 LIVE 환경에서는 기존 주문의 private GET이 발생합니다.

전체 로그를 따라가야 할 때:

```bash
docker compose logs -f
```

## 서버 런타임 안전 점검

제한적 라이브 테스트 전이나 운영 상태 점검 시 서버 런타임 안전 점검 스크립트를 실행합니다.

일반 점검:

```bash
bash scripts/check_server_runtime_safety.sh
```

제한적 라이브 테스트 직전 엄격 점검:

```bash
bash scripts/check_server_runtime_safety.sh --strict-live
```

현재 scheduled Production LIVE 점검:

```bash
bash scripts/check_server_runtime_safety.sh --production-live
```

이 스크립트는 읽기 전용 점검만 수행합니다. 컨테이너를 시작, 중지, 재생성, 삭제하지 않으며 주문도 실행하지 않습니다.

## 수동 AI 분석 실행

서버에서 1회 AI 분석을 수동 실행합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis
```

이 명령은 시장/계좌/캔들 데이터를 수집하고 AI 추천을 생성한 뒤 Telegram 알림 또는 승인 요청을 보냅니다.

## STATIC/DYNAMIC rollout

배포 직후에는 반드시 기본 `MARKET_UNIVERSE_MODE=STATIC`과
`LIVE_DYNAMIC_MARKET_ENABLED=false`를 유지합니다. 이 상태에서는 기존
`ALLOWED_MARKETS`가 분석 목록과 LIVE 주문 allowlist 역할을 계속합니다.

DYNAMIC 분석을 검증할 때는 먼저 주문 모드를 MOCK으로 유지하고
`MARKET_UNIVERSE_MODE=DYNAMIC`만 켠 뒤 다음 진단을 실행합니다.

```bash
docker compose --profile manual run --rm ai-trade-analysis \
  python -m scripts.check_market_universe
```

진단은 OpenAI나 주문 API를 호출하지 않습니다. 전체/제외/prefilter/Top N/holdings 추가
수와 timeframe별 data quality를 확인합니다. DYNAMIC LIVE는 기존
`LIVE_ORDER_ENABLED`, confirmation, Telegram 승인에 더해
`LIVE_DYNAMIC_MARKET_ENABLED=true`가 있어야 하며, 운영 검토 없이 이 값을 켜지
않습니다.

## PostgreSQL 공개 포트 확인

서버 런타임에서는 PostgreSQL이 외부에 공개되면 안 됩니다.

```bash
docker compose ps postgres
```

`PORTS`에 다음처럼 내부 포트만 보이는 것은 정상입니다.

```text
5432/tcp
```

서버에서 다음처럼 보이면 안 됩니다.

```text
0.0.0.0:5432->5432/tcp
```

위와 같은 외부 포트 매핑이 보이면 즉시 Compose 파일 조합을 확인합니다. 서버에서는 `docker-compose.local.yml`을 함께 사용하지 않습니다.

## Operational Alerting 운영

`operational-alert-worker`는 DB와 Telegram만 사용하는 별도 프로세스입니다. 거래,
reconciliation, portfolio/PnL transaction과 결합되지 않으며 Upbit/OpenAI client를
사용하지 않습니다. 기본 설정은 다음과 같습니다.

```env
OPERATIONAL_ALERTING_ENABLED=false
OPERATIONAL_ALERT_INTERVAL_SECONDS=60
LIVE_ORDER_STALE_ALERT_AFTER_SECONDS=600
OPERATIONAL_ALERT_MAX_RETRIES=3
OPERATIONAL_ALERT_RETRY_DELAYS_MINUTES=1,5,15
```

활성화 전 읽기 전용 진단으로 stale LIVE 주문과 pending/retryable outbox 수를 확인합니다.

```bash
python -m scripts.check_operational_alerts
```

진단은 INSERT/UPDATE/commit과 Telegram/Upbit/OpenAI 호출을 하지 않습니다. Worker는
LIVE·UPBIT의 `LIVE_PLACED`, `LIVE_WAIT`, `LIVE_UNKNOWN`만 `OrderLog.created_at` 기준으로
감지합니다. 주문당 deterministic dedup key를 사용해 경고는 한 번만 만들고, 주문이
terminal이 되면 alert의 `resolved_at`만 갱신합니다. 주문이나 추천 상태는 바꾸지 않습니다.

권장 Production rollout 순서는 deploy와 migration 완료, dry-run 결과 확인,
`OPERATIONAL_ALERTING_ENABLED=true` 설정, worker recreate, 로그 확인, runtime safety
검사입니다. 설정과 worker 활성화는 운영자가 직접 수행합니다.

```bash
docker compose up -d --no-deps --force-recreate operational-alert-worker
docker compose logs --tail=100 operational-alert-worker
bash scripts/check_server_runtime_safety.sh --production-live
```

## Upbit Order Chance rollout

Order Chance preflight는 `LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED=false`가 기본입니다.
비활성화 상태에서는 기존 LIVE 실행과 account/ticker 검증이 그대로 유지됩니다. 활성화하면
기존 OrderLog/identifier recovery 이후 새 주문을 만들 때만 인증된
`GET /v1/orders/chance`를 호출하고, 실패하거나 응답이 불완전하면 fail-closed합니다.

추천 단계의 5,000원 기준은 네트워크 없는 conservative pre-screen입니다. 활성화된 실제
LIVE 실행에서는 chance의 current min/max/type/balance/fee가 canonical preflight이며,
내부 건별·일일 BUY 한도는 별도로 계속 적용됩니다. 승인된 BUY 금액이나 SELL 수량은
자동으로 줄이지 않습니다.

Production rollout은 다음 순서를 사용합니다.

1. 배포 후 `LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED=false`를 확인합니다.
2. `bash scripts/check_server_runtime_safety.sh --production-live`를 실행합니다.
3. 아래 read-only 진단 결과를 검토합니다.
4. `.env`를 백업한 뒤 운영자가 flag를 `true`로 변경합니다.
5. Telegram listener 등 설정을 읽는 필요한 container를 recreate합니다.
6. runtime safety와 container 로그를 다시 확인합니다.
7. 별도 인위적 주문 없이 자연스러운 다음 Telegram 승인 주문에서 관찰합니다.

```bash
python -m scripts.check_upbit_order_chance --market KRW-BTC
```

진단은 `GET /v1/orders/chance`만 호출하며 `/v1/orders`, `/v1/orders/test`, DB write,
Telegram, OpenAI 호출을 하지 않습니다. Chance GET 실패는 주문 생성 전이므로
`LIVE_FAILED`; 실제 POST 응답 불명확만 기존 `LIVE_UNKNOWN`입니다.

## Account Activity Ledger rollout

Account Activity sync는 `GET /v1/orders/closed`, `GET /v1/deposits`,
`GET /v1/withdraws`만 사용합니다. 주문조회, 입금조회, 출금조회 권한은 서로 독립적이며
출금조회 권한은 출금하기 권한이 아닙니다. 주소, TXID, Authorization, JWT는 ledger나
출력에 저장하지 않습니다.

Production rollout 순서:

1. 배포와 migration 후 `ACCOUNT_ACTIVITY_SYNC_ENABLED=false`를 확인합니다.
2. runtime safety를 실행합니다.
3. `python -m scripts.check_upbit_account_activity_access`로 세 조회 권한을 확인합니다.
4. `python -m scripts.sync_account_activities --start-at <ISO-8601>` dry-run을 실행합니다.
5. source별 count, BOT/EXTERNAL 분류, coverage와 `OUT_OF_SCOPE`를 검토합니다.
6. DB backup 후 운영자가 같은 범위에 `--apply`를 한 번 명시합니다.
7. DB row 수, unique key와 source별 sync state를 검증합니다.
8. 지속 동기화가 필요할 때만 flag를 `true`로 변경하고 worker를 recreate합니다.
9. worker 로그와 runtime safety를 다시 확인합니다.

기본 dry-run은 remote GET과 DB SELECT만 수행하며 flush/commit/cursor update가 없습니다.
`--apply`는 source별 짧은 transaction으로 activity upsert와 coverage state를 저장합니다.
한 source가 `OUT_OF_SCOPE`여도 다른 source는 독립적으로 동기화되지만, 전체 결과는
complete로 표시되지 않습니다.

## Production scheduler 관리

Production scheduled LIVE에서는 scheduler profile을 명시해 실행합니다.

```bash
docker compose --profile scheduler up -d ai-trade-scheduler
```

배포 또는 장애 조사 중 분석 실행을 막아야 할 때만 중지합니다.

```bash
docker compose --profile scheduler stop ai-trade-scheduler
```

중지 후 상태를 확인합니다.

```bash
docker compose --profile scheduler ps -a ai-trade-scheduler
```

## 오래된 종료 상태 스케줄러 컨테이너 제거

종료된 스케줄러 컨테이너가 남아 있고 정리가 필요하면 제거합니다.

```bash
docker compose --profile scheduler rm ai-trade-scheduler
```

실행 중인 컨테이너를 제거하지 않도록 먼저 `ps -a` 상태를 확인합니다.

## DB 백업 실행

서버에서 PostgreSQL 컨테이너가 실행 중인 상태에서 백업 스크립트를 실행합니다.

```bash
bash scripts/backup_db.sh
```

백업 파일은 다음 디렉터리에 생성됩니다.

```bash
${HOME}/backups
```

기본적으로 최근 10개 백업만 유지합니다. 보관 개수를 바꾸려면 다음처럼 실행합니다.

```bash
BACKUP_KEEP_COUNT=20 bash scripts/backup_db.sh
```

## 백업 파일 확인

```bash
ls -lh ${HOME}/backups/crypto_trading_bot_*.sql
```

백업 파일 권한은 `600`이어야 합니다.

```bash
ls -l ${HOME}/backups/crypto_trading_bot_*.sql
```

## 위험한 명령 주의

다음 명령은 컨테이너와 네트워크뿐 아니라 PostgreSQL 데이터 볼륨까지 삭제합니다.

```bash
docker compose down -v
```

`postgres_data` 볼륨이 삭제되면 데이터베이스 내용이 사라집니다. 서버에서는 이 명령을 가볍게 실행하지 않습니다.

일반적인 중지는 다음 명령을 사용합니다.

```bash
docker compose down
```

## AI 거래 비율과 승인

- `trade_ratio=1`의 BUY는 현재 KRW 100%를 의미하지만 BUY 최대 한도를 유지합니다.
- 수수료를 임의로 하드코딩하지 않으므로, 잔고 전체 BUY의 정확한 최종 가능액은
  향후 Upbit order-chance/수수료 정보 연동이 필요합니다.
- `trade_ratio=1`의 SELL은 추천 산정 시점 선택 자산 수량 100%를 승인 수량으로 사용합니다.
- SELL은 BUY용 최대 금액과 일일 BUY 한도를 적용하지 않습니다.
- 승인 후 SELL 실행 직전에 ticker와 잔고를 재조회하며, 승인한 수량을
  임의로 늘리거나 줄이지 않습니다.
- 누락, NaN, 무한대, 음수, 1 초과 ratio는 승인 요청을 만들지 않도록 HOLD로
  안전 변환됩니다.

## 단순 복구 메모

- 최신 서버 `.env`와 `/etc/crypto-trading-bot/secrets`를 안전하게 보관합니다.
- DB 백업 파일을 유지합니다.
- 복구는 자동으로 처리하지 말고, 상황을 확인한 뒤 수동으로 신중하게 진행합니다.
- 복구 전에는 현재 컨테이너 상태, 백업 파일 시각, `.env`와 비밀 디렉터리를 다시 확인합니다.
