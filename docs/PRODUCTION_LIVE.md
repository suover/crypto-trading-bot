# Production scheduled LIVE 운영 및 배포

이 문서는 `/home/ubuntu/apps/crypto-trading-bot`에서 실행되는 현재 Production 운영 절차를 설명합니다. 첫 제한적 LIVE 주문 검증은 [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md)를 사용합니다.

## 주문 흐름과 안전 경계

Production 흐름은 다음과 같습니다.

1. `ai-trade-scheduler`가 설정된 시각에 AI 분석을 시작합니다.
2. DYNAMIC Market Universe와 multi-timeframe feature를 바탕으로 AI가 BUY/SELL/HOLD와 `trade_ratio`를 결정합니다.
3. 애플리케이션이 정확한 BUY 금액 또는 SELL 수량을 계산합니다.
4. BUY/SELL은 Telegram 승인 요청을 생성하고 HOLD는 주문을 만들지 않습니다.
5. 사용자가 Telegram에서 승인한 BUY/SELL만 Upbit LIVE 주문 실행 단계로 이동합니다.

예약 분석은 자동으로 실행되지만 실제 주문은 완전 자동매매가 아닙니다. Telegram 승인, LIVE 활성화 플래그, 주문 한도, persisted universe eligibility, idempotency와 reconciliation 안전장치가 계속 적용됩니다.

## Production Compose 구조

- 기본 runtime: `postgres`, `migrate`, `telegram-listener`, `mock-order-retry-worker`, `live-order-reconciliation-worker`
- `scheduler` profile: `ai-trade-scheduler`
- `manual` profile: 일회성 `ai-trade-analysis`

`ai-trade-analysis`는 상시 실행 서비스가 아닙니다. 배포 때 이미지는 build하지만 컨테이너를 계속 실행하지 않습니다.

## Production 안전 점검

```bash
cd /home/ubuntu/apps/crypto-trading-bot
bash scripts/check_server_runtime_safety.sh --production-live
```

점검 대상은 다음과 같습니다.

- 저장소 루트, `.env`, Compose 구성
- `.env` 권한 600
- PostgreSQL running/healthy 및 호스트 포트 미노출
- Telegram listener, mock retry worker, LIVE reconciliation worker, scheduler 실행 상태
- `LIVE_ORDER_RECONCILIATION_ENABLED=true` (미설정 시 기본 true)
- DB 백업 존재 여부와 파일 권한 600
- `SECRET_DIR` 존재 및 권한 700
- 필수 secret 파일 존재·비어 있지 않음·권한 600
- `TRADING_MODE=AI_APPROVAL`
- `ORDER_EXECUTION_MODE=LIVE`
- `LIVE_ORDER_ENABLED=true`
- 정확한 `LIVE_ORDER_CONFIRMATION`
- DYNAMIC 사용 시 `LIVE_DYNAMIC_MARKET_ENABLED=true`
- scheduler 설정과 실행 시각
- 건별/일일 BUY 한도의 양수 여부

비밀값은 점검 출력에 포함하지 않습니다. Production 점검은 특정 주문 금액을 하드코딩하지 않고 구조적 유효성만 확인합니다. 일일 BUY 한도가 건별 BUY 한도보다 작은 구성도 더 보수적인 유효한 안전 정책으로 허용합니다.

## 서버 수동 배포

배포 전 서버는 `main` 브랜치이고 working tree가 clean이어야 합니다.

```bash
cd /home/ubuntu/apps/crypto-trading-bot
bash scripts/deploy_production.sh
```

특정 commit만 허용하려면 전체 40자리 SHA를 전달합니다.

```bash
bash scripts/deploy_production.sh --expected-sha <40-character-main-SHA>
```

스크립트 동작 순서:

1. 저장소 root, `main`, clean working tree 확인
2. origin과 필수 명령 확인, 현재 commit 기록
3. default/manual/scheduler Compose config 검증
4. PostgreSQL running/healthy 확인
5. `git fetch origin main` 및 expected SHA 검증
6. `scripts/backup_db.sh`로 DB 백업
7. `${HOME}/backups`에 `.env`를 권한 600으로 백업
8. scheduler 중지로 Production cutover 시작
9. `git merge --ff-only origin/main` 및 최종 HEAD expected SHA 재검증
10. migrate, listener, retry/reconciliation worker, manual analysis, scheduler 이미지 전체 build
11. listener와 retry/reconciliation worker 중지
12. Alembic migration 컨테이너 실행
13. listener와 retry/reconciliation worker 재생성
14. scheduler 재생성
15. container health 확인
16. `--production-live` 최종 점검

실패하면 non-zero로 종료하고 자동 Git rollback, DB rollback, migration downgrade를 수행하지 않습니다. 중지된 listener/worker/scheduler를 실패 처리 중 자동 재기동하지 않으며, scheduler가 재기동된 뒤 최종 점검이 실패하면 다시 중지하여 사람이 상태를 확인하도록 합니다.

## GitHub Actions 수동 Production CD

GitHub Actions의 `Deploy Production` workflow는 `workflow_dispatch`로만 실행됩니다.

1. Actions 화면에서 main의 `Deploy Production`을 선택합니다.
2. confirmation에 정확히 `DEPLOY`를 입력합니다.
3. workflow는 실행 대상 main의 exact SHA를 고정합니다.
4. SSH로 서버의 `origin/main`이 동일 SHA인지 검증합니다.
5. 아직 서버 working tree에 새 배포 스크립트가 없어도 `git show <SHA>:scripts/deploy_production.sh`로 임시 추출합니다.
6. 임시 스크립트를 `--expected-sha`와 함께 실행합니다.

동시 Production 배포는 GitHub Actions concurrency로 직렬화됩니다. workflow 실행 도중 main이 변경되어 `origin/main`이 고정 SHA와 달라지면 배포를 중단합니다.

## 필요한 GitHub Secrets

- `PROD_HOST`
- `PROD_USER`
- `PROD_SSH_PRIVATE_KEY`
- `PROD_SSH_KNOWN_HOSTS`
- `PROD_SSH_PORT` — 선택 사항, 미설정 시 22

`PROD_SSH_KNOWN_HOSTS`는 신뢰할 수 있는 경로에서 사전 검증한 서버 host key여야 합니다. workflow는 `StrictHostKeyChecking=yes`를 사용합니다.

가능하면 GitHub `production` Environment에 required reviewer를 설정해 workflow dispatch 이후에도 사람의 승인을 한 번 더 요구합니다.

## EC2 사전 설정

- 배포 사용자가 `/home/ubuntu/apps/crypto-trading-bot`과 Docker를 사용할 수 있어야 합니다.
- 저장소 origin이 `suover/crypto-trading-bot`의 main을 가리켜야 합니다.
- private repository라면 서버에 repository read-only GitHub Deploy Key를 사전 등록합니다.
- Deploy Key의 private key는 서버에만 두고 GitHub Actions SSH key와 분리합니다.
- HTTPS origin이 username/password prompt를 요구하는 구성은 비대화식 `git fetch`를 중단시킬 수 있으므로 사용하지 않습니다.
- `.env` 권한은 600, `SECRET_DIR` 권한은 700, secret 파일 권한은 600이어야 합니다.
- Upbit API key에는 필요한 조회/주문 권한만 부여하고 출금 권한은 부여하지 않습니다.

이 작업은 Deploy Key를 자동 생성하거나 서버 인증을 변경하지 않습니다.

## 백업과 배포 후 확인

DB 백업은 `${HOME}/backups/crypto_trading_bot_*.sql`, 환경 백업은 `${HOME}/backups/crypto_trading_bot_env_*.env`에 생성됩니다. 두 파일 유형 모두 권한 600으로 관리합니다.

배포 완료 후 다음을 확인합니다.

```bash
docker compose --profile manual --profile scheduler ps -a
docker compose logs --tail=100 migrate
docker compose logs --tail=100 telegram-listener
docker compose logs --tail=100 mock-order-retry-worker
docker compose logs --tail=100 live-order-reconciliation-worker
docker compose --profile scheduler logs --tail=100 ai-trade-scheduler
bash scripts/check_server_runtime_safety.sh --production-live
```

## 기존 LIVE 주문 자동 reconciliation

`live-order-reconciliation-worker`는 이미 존재하는 LIVE·UPBIT 주문 중 `LIVE_PLACED`, `LIVE_WAIT`, `LIVE_UNKNOWN`만 조회합니다. UUID가 없으면 `recommendation-{recommendation_id}`로 기존 주문을 GET합니다. **새 BUY/SELL 생성, 자동 재주문, 자동 주문 취소, 추천/Telegram 승인 생성, AI 호출은 하지 않습니다.** MOCK retry worker와 역할이 분리되어 있습니다.

| Upbit 상태 | OrderLog | TradeRecommendation |
| --- | --- | --- |
| 접수 후 미확정 | LIVE_PLACED | LIVE_EXECUTION_PENDING |
| wait / watch | LIVE_WAIT | LIVE_EXECUTION_PENDING |
| done | LIVE_DONE | LIVE_EXECUTED |
| cancel, executed_volume > 0 | LIVE_EXECUTED_CANCELLED | LIVE_EXECUTED |
| cancel, 체결 없음 | LIVE_CANCELLED | LIVE_EXECUTION_CANCELLED |
| 생성 실패 | LIVE_FAILED | LIVE_EXECUTION_FAILED |
| 생성 결과 불명 | LIVE_UNKNOWN | LIVE_EXECUTION_UNKNOWN |

체결 없는 취소는 실행 완료로 표시하지 않습니다. 기존 상태 매핑의 유한 양수 executed_volume 판정을 유지합니다. Worker는 terminal 상태를 polling하지 않으며, 일시적 조회 오류나 order-not-found는 기존 상태를 바꾸지 않고 다음 cycle에 다시 확인합니다. DB 등 치명적 오류는 non-zero 종료하고 컨테이너 재시작 정책에 맡깁니다. 로그에는 주문/추천 ID, 결과, 상태, 오류 종류/HTTP 상태만 출력합니다.

설정 기본값:

```env
LIVE_ORDER_RECONCILIATION_ENABLED=true
LIVE_ORDER_RECONCILIATION_INTERVAL_SECONDS=60
LIVE_ORDER_RECONCILIATION_BATCH_SIZE=20
```

interval 범위는 10–3600초, batch 범위는 1–100입니다. 순차 GET 사이에는 0.2초 간격을 둡니다. advisory lock key `2026082801`로 단일 worker를 보장하며, 각 주문은 실행 서비스와 같은 추천→주문 row-lock 순서로 재확인합니다. 잠긴 주문은 다음 순환으로 미룹니다. 프로세스 내 순환 cursor로 오래된 UNKNOWN이 뒤의 주문을 계속 가리지 않게 합니다.

MOCK/disabled 환경에서는 DB polling과 Upbit GET 없이 대기합니다. 기존 주문 추적은 신규 주문용 `LIVE_ORDER_ENABLED`/confirmation에 의존하지 않지만 `ORDER_EXECUTION_MODE=LIVE`여야 합니다. 점검 중에도 실제 private GET을 원치 않으면 worker를 비활성화하세요.

```bash
python -m scripts.run_live_order_reconciliation_worker --once
python -m scripts.check_live_order_status --recommendation-id <id>
```

수동 조회는 기존대로 지원하며 audit의 `reconciliation_source`는 `MANUAL` 또는 `WORKER`입니다. 성공적인 재조회 시 기존 `manual_reconciliation` boolean을 이 source와 `reconciled_at`으로 대체합니다. 이 상태/audit 형식 변경 자체에는 migration이 필요하지 않습니다. 과거 terminal 주문의 잘못된 추천 상태를 일괄 변경하지는 않으며 필요 시 수동 조회로 정정합니다.

## LIVE execution/fill ledger

`order_logs.amount_krw`, `quantity`, `price`는 주문 요청/승인 값입니다. 실제 체결 결과는 별도 nullable 컬럼인 `executed_quantity`, `executed_funds_krw`, `average_execution_price`, `paid_fee`, `remaining_quantity`, `trades_count`, `execution_synced_at`에 저장합니다. `execution_synced_at`은 Upbit 체결시각이 아니라 시스템이 응답을 DB에 마지막으로 동기화한 시각입니다.

실제 체결금액은 정상적인 `executed_funds`를 우선 사용하고, 없을 때만 유효한 `trades[].funds` 합계를 사용합니다. 평균 체결가는 실제 체결금액을 실제 체결수량으로 나눈 값이며 요청금액·ticker·주문 price로 추정하지 않습니다. 개별 `trades[]`는 `order_fills`에 저장되고 exchange trade UUID의 unique constraint로 반복 reconciliation 중복을 막습니다. 기존 `raw_response` audit는 삭제하거나 축약하지 않습니다.

과거 데이터 backfill은 저장된 `raw_response.order_status_response`만 사용하며 기본값은 dry-run입니다. 네트워크, 주문, Telegram, AI 호출은 하지 않습니다. Production에서 `--apply`는 이번 기능 배포와 별개의 승인된 운영 작업으로 취급합니다.

```bash
python -m scripts.backfill_live_execution_ledger
python -m scripts.backfill_live_execution_ledger --apply
```

## Account-level Portfolio valuation

예약 분석은 동일 pipeline에 저장된 account snapshot과 universe market snapshot을 사용해 AI 추천 전에 `PortfolioSnapshot`과 `PortfolioPositionSnapshot`을 생성합니다. 이 단계는 DB-only이며 Upbit/OpenAI/Telegram을 새로 호출하지 않습니다. 신규 account run type은 `ACCOUNT_SNAPSHOT`이고 기존 `MANUAL` account run은 legacy source로 계속 읽을 수 있습니다.

`known_total_value_krw`는 가격이 확인된 범위의 합계입니다. 보유자산 하나라도 동일 pipeline 가격이 없으면 상태는 `PARTIAL`, `total_value_krw`는 NULL이며 이전 pipeline 가격으로 보충하지 않습니다. 가격이 모두 있어도 avg buy price가 빠지면 계좌 가치 평가는 COMPLETE일 수 있지만 aggregate cost basis와 미실현손익은 NULL입니다.

이 snapshot은 실제 Upbit 계좌 전체의 현재 평가이며 봇 성과가 아닙니다. `OrderFill`은 봇이 생성한 LIVE 주문 체결 원장이므로 두 데이터 계층을 직접적인 수익률로 결합하지 않습니다.

UNKNOWN이 영구히 조회되지 않으면 자동 재주문/취소하지 않고 미확정으로 남습니다. 반복 오류 로그는 운영자가 조사해야 합니다. cursor는 재시작하면 초기화되며, 여러 사용자별 Upbit 계정을 라우팅하는 worker가 아니라 현재 배포에 설정된 단일 Upbit 계정용입니다.

## 실패 시 수동 대응

- 실패 단계, 이전 commit, 현재 commit, scheduler 상태를 먼저 기록합니다.
- scheduler가 중지돼 있으면 원인을 확인하기 전에 임의로 다시 시작하지 않습니다.
- migration이 실행됐다면 자동 downgrade하지 않습니다.
- `LIVE_UNKNOWN` 주문은 Upbit 주문 내역과 reconciliation 결과를 확인하기 전 재주문하지 않습니다.
- 자동 rollback 대신 현재 DB/컨테이너/Git 상태와 백업을 바탕으로 사람이 복구 방향을 결정합니다.

필요하면 기존 수동 명령으로 fallback할 수 있지만 반드시 DB와 `.env` 백업, fast-forward-only Git 업데이트, 전체 profile image build, migration, health check 순서를 유지합니다.

## 절대 금지

Production에서 다음 명령을 실행하지 않습니다.

```bash
docker compose down -v
```

이 명령은 PostgreSQL volume을 삭제합니다. 배포 스크립트도 `down`, volume 삭제, Git 강제 reset을 사용하지 않습니다.
