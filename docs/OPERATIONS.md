# 운영 가이드

이 문서는 repository와 server runtime의 일상 운영 source of truth입니다. Production 배포와 LIVE safety는 [PRODUCTION_LIVE.md](PRODUCTION_LIVE.md), 첫 제한적 실제 주문은 [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md)를 먼저 따르세요. Research 방법론과 policy lifecycle은 각각 [STRATEGY_RESEARCH.md](STRATEGY_RESEARCH.md), [POLICY_PROMOTION.md](POLICY_PROMOTION.md)에 있습니다.

## 명령 실행 위치

### Repository root

다음은 repository root에서 실행합니다.

```text
docker compose ...
bash scripts/backup_db.sh
bash scripts/check_server_runtime_safety.sh
bash scripts/deploy_production.sh
```

`.env`, `.secrets/`, `docker-compose.yml`도 root에 있습니다.

### `trading-engine/`

Python, uv, Alembic과 `scripts.*` module은 engine directory에서 실행합니다.

```bash
cd trading-engine
uv run alembic current
uv run python -m scripts.show_latest_trade_flow_status
```

## Server update 원칙

수동 Production update 전에 [PRODUCTION_LIVE.md](PRODUCTION_LIVE.md)의 전체 절차를 확인합니다. 최소 원칙은 다음과 같습니다.

- `main` branch와 clean working tree를 확인합니다.
- `origin/main`을 fetch하고 fast-forward만 허용합니다.
- Compose configuration, PostgreSQL running/health와 expected SHA를 service 중지 전에 검사합니다.
- DB와 `.env` backup이 성공한 뒤에만 cutover를 시작합니다.
- Scheduler를 build/cutover 전에 중지해 새 분석 시작을 막습니다.
- Application image 전체를 build한 뒤 runtime worker downtime을 최소화합니다.
- Migration, runtime recreate, health check와 `--production-live` 검증을 완료합니다.
- 자동 Git/DB rollback은 수행하지 않습니다.

권장 경로는 root deploy script입니다.

```bash
bash scripts/deploy_production.sh
```

GitHub Actions에서 전달한 exact SHA를 강제하려면:

```bash
bash scripts/deploy_production.sh --expected-sha <40_CHARACTER_SHA>
```

## Secret 준비

실제 secret은 repository 밖의 restricted directory를 `SECRET_DIR`로 지정하거나 Git ignored `.secrets/`에 둡니다. Commit, 문서, shell history나 diagnostic output에 값을 쓰지 마세요.

Compose가 기대하는 file name:

```text
postgres_password
openai_api_key
telegram_bot_token
upbit_access_key
upbit_secret_key
```

Production 권장 권한은 directory `700`, 각 file과 `.env` `600`입니다. `.env`에는 가능한 경우 `_FILE` 경로만 둡니다. 설정 목록과 safe default는 [`.env.example`](../.env.example)이 source of truth입니다.

## Compose validation

실행 전에 모든 사용 profile을 검증합니다.

```bash
docker compose config --quiet
docker compose --profile manual config --quiet
docker compose --profile scheduler config --quiet
docker compose --profile manual --profile scheduler config --quiet
```

`docker-compose.local.yml`은 개발 환경에서만 PostgreSQL host port를 공개합니다. Production-like 기본 Compose는 PostgreSQL을 Docker network 내부에만 둡니다.

## Runtime 시작과 확인

### Default runtime

```bash
docker compose up -d
docker compose ps -a
```

Default runtime에는 PostgreSQL, migration과 listener/worker container가 포함됩니다. Analytics worker는 container가 실행 중이어도 해당 `*_ENABLED=false`이면 idle할 수 있습니다.

### Scheduler

```bash
docker compose --profile scheduler up -d ai-trade-scheduler
docker compose logs --tail 200 ai-trade-scheduler
```

Scheduler는 KST의 `AI_ANALYSIS_SCHEDULE_TIMES`를 사용하고 PostgreSQL advisory lock으로 중복 instance를 막습니다. Scheduled analysis도 Telegram approval 없이 주문하지 않습니다.

### Manual analysis

```bash
docker compose --profile manual run --rm ai-trade-analysis
```

이 명령은 market/account API, OpenAI와 Telegram을 사용할 수 있습니다. LIVE 주문은 여전히 approval과 execution guard가 필요하지만, 단순 smoke test로 실행하기 전에 외부 호출 범위를 확인하세요.

## Logs

```bash
docker compose logs --tail 200 telegram-listener
docker compose logs --tail 200 live-order-reconciliation-worker
docker compose logs --tail 200 operational-alert-worker
docker compose logs --tail 200 recommendation-outcome-worker
docker compose logs --tail 200 ai-trade-scheduler
```

Secret value나 complete authenticated response를 issue/report에 복사하지 마세요. Operational error message는 safe classification을 사용하지만 운영자는 출력 내용을 다시 검토해야 합니다.

## Runtime safety check

```bash
# 일반 점검
bash scripts/check_server_runtime_safety.sh

# 첫 5,000 KRW 제한 검증
bash scripts/check_server_runtime_safety.sh --strict-live

# scheduled approval-based Production LIVE
bash scripts/check_server_runtime_safety.sh --production-live
```

`--production-live`는 repository/config/secret permission, Compose, DB health, runtime container, trading user, reconciliation, scheduler, LIVE flags와 양수인 주문 한도를 fail-closed로 검사합니다. 일일 BUY 한도가 건별 한도보다 작은 구성은 더 보수적인 유효 정책이며 warning일 뿐 실패가 아닙니다.

## DB backup

```bash
bash scripts/backup_db.sh
```

Backup은 실행 중인 `crypto-trading-postgres`에서 `pg_dump`를 수행하고 `${HOME}/backups`에 권한 `600`으로 저장합니다. 기본적으로 최근 10개를 유지합니다. 보존 수는 1 이상의 `BACKUP_KEEP_COUNT`로 조정할 수 있습니다.

```bash
BACKUP_KEEP_COUNT=20 bash scripts/backup_db.sh
ls -la "$HOME/backups"
```

운영 DB를 대상으로 임의 downgrade하지 마세요. Migration 왕복 검증은 폐기 가능한 로컬 PostgreSQL에서만 수행합니다.

## Worker rollout과 opt-in flag

| Worker | Enable flag | 기본값 | External access |
| --- | --- | --- | --- |
| Reconciliation | `LIVE_ORDER_RECONCILIATION_ENABLED` | `true` | Upbit private order GET |
| Account Activity | `ACCOUNT_ACTIVITY_SYNC_ENABLED` | `false` | Upbit authenticated GET |
| Bot PnL | `BOT_TRADING_PNL_ENABLED` | `false` | 없음 |
| Portfolio Performance | `PORTFOLIO_PERFORMANCE_ENABLED` | `false` | 필요 시 Upbit public 1-minute candle |
| Operational Alert | `OPERATIONAL_ALERTING_ENABLED` | `false` | Telegram |
| Recommendation Outcome | `RECOMMENDATION_OUTCOME_ENABLED` | `false` | Upbit public candle |
| Research Candidate Outcome | `RESEARCH_CANDIDATE_OUTCOME_ENABLED` | `false` | Upbit public candle |
| Shadow Selection automation | `SHADOW_SELECTION_EVALUATION_ENABLED` | `false` | 없음 |

Flag를 바꾼 뒤에는 필요한 service만 명시적으로 recreate하고 logs와 safety check를 확인합니다. 설정 변경 전 `.env`를 권한 `600`으로 backup합니다. 이 문서는 실제 환경 값을 지정하지 않습니다.

Portfolio Performance worker는 PostgreSQL과 DB secret만 사용하지만 non-KRW completed external cash flow 평가에는 Upbit public candle을 조회할 수 있습니다. Upbit private API, OpenAI와 Telegram은 사용하지 않습니다.

## Diagnostics

아래 명령은 `trading-engine/`에서 실행합니다. 이름에 `check`가 있어도 authenticated/public API 여부가 다르므로 설명을 확인하세요.

### DB-only/read-only

```bash
uv run python -m scripts.show_latest_trade_flow_status
uv run python -m scripts.report_strategy_replay_dataset --limit 20
uv run python -m scripts.report_recommendation_outcomes
uv run python -m scripts.report_research_candidate_outcomes --limit 20
uv run python -m scripts.check_live_ranking_policy \
  --user-id <USER_ID> --exchange UPBIT --quote-asset KRW
```

### Public market data

```bash
uv run python -m scripts.check_upbit_ticker
uv run python -m scripts.check_market_universe
uv run python -m scripts.check_market_indicators
```

Dynamic Universe diagnostic은 public market/ticker/candle/orderbook만 사용하며 OpenAI, Telegram과 주문 API를 호출하지 않습니다. `.env`를 바꾸지 않고 command-level environment override로 DYNAMIC을 검사할 수 있습니다.

### Authenticated GET-only

```bash
uv run python -m scripts.check_upbit_order_chance --market KRW-BTC
uv run python -m scripts.check_upbit_account_activity_access
```

이 명령들은 주문을 생성하지 않지만 실제 Upbit credential과 network를 사용합니다. 권한과 rate limit을 고려해 운영자가 의도적으로 실행해야 합니다. `check_upbit_order_test`는 거래소 test-order endpoint를 호출하므로 일반 진단에 포함하지 않습니다.

## Reconciliation 운영

`live-order-reconciliation-worker`는 existing LIVE order status를 조회할 뿐 생성·retry·cancel하지 않습니다.

```bash
docker compose logs --tail 200 live-order-reconciliation-worker
```

한 recommendation을 수동 조회할 때:

```bash
cd trading-engine
uv run python -m scripts.check_live_order_status --recommendation-id <ID>
```

`LIVE_UNKNOWN`, stale alert, identifier conflict가 있으면 새 주문을 만들지 말고 DB의 recommendation, attempt, order log와 Upbit order 조회를 함께 확인합니다. 처리 순서는 [PRODUCTION_LIVE.md](PRODUCTION_LIVE.md)의 recovery 절차를 따릅니다.

## Account Activity와 analytics rebuild

Account Activity 수동 sync는 기본 dry-run입니다.

```bash
uv run python -m scripts.sync_account_activities \
  --user-id <USER_ID> --start-at <ISO_TIMESTAMP> --end-at <ISO_TIMESTAMP>
```

`--apply`는 source ledger를 upsert하므로 preview와 범위를 먼저 검토합니다. 실제 Upbit private GET을 사용하지만 주문을 만들지 않습니다.

Derived analytics rebuild도 기본 dry-run인 경우가 많습니다.

```bash
uv run python -m scripts.rebuild_bot_trading_pnl
uv run python -m scripts.rebuild_portfolio_performance
uv run python -m scripts.rebuild_recommendation_outcomes --limit 20
```

각 CLI의 `--help`로 write/external-call behavior를 확인한 뒤 `--apply`를 사용하세요. Source ledger를 임의 수정하거나 PARTIAL을 0으로 대체하지 않습니다.

## Research와 policy operation

Research CLI와 read/write matrix는 [STRATEGY_RESEARCH.md](STRATEGY_RESEARCH.md)에 있습니다. Candidate enrollment, Shadow evaluation, human approval, Full LIVE activation/termination은 [POLICY_PROMOTION.md](POLICY_PROMOTION.md)가 source of truth입니다.

다음 원칙을 유지합니다.

- Sweep 결과만으로 candidate를 자동 선택하거나 LIVE로 적용하지 않습니다.
- Preview와 exact expected signature를 확인한 뒤에만 apply합니다.
- Human approval과 Full LIVE activation은 별도 단계입니다.
- Activation은 주문 생성이 아니라 다음 분석의 ranking policy 선택입니다.
- Environment-specific candidate ID나 현재 row 상태를 일반 문서에 고정하지 않습니다.

## Common recovery

1. 새 분석과 새 주문을 시작하지 않도록 scheduler 상태를 확인합니다.
2. `docker compose ps -a`와 관련 worker logs를 확인합니다.
3. `show_latest_trade_flow_status`로 recommendation → approval → attempt → order 흐름을 확인합니다.
4. LIVE order가 모호하면 identifier/reconciliation 결과를 확인하고 중복 주문을 만들지 않습니다.
5. DB schema/runtime mismatch는 backup과 현재 Alembic revision을 확인한 뒤 대응합니다.
6. 자동 reset, destructive downgrade, DB row 수동 조작으로 상태를 숨기지 않습니다.

```bash
cd trading-engine
uv run alembic current
uv run alembic heads
```

## 종료

개발 runtime을 종료할 때:

```bash
docker compose --profile manual --profile scheduler down
```

Volume 삭제 옵션은 포함하지 않습니다. Production에서는 일반 개발 종료 명령을 실행하지 말고 service별 상태와 배포/runbook 절차를 따릅니다.

## 금지 사항

- 실제 secret, private key, chat ID나 server identifier를 문서/commit/log에 복사하지 않습니다.
- 운영 DB에 검증 목적의 downgrade를 실행하지 않습니다.
- 모호한 LIVE 상태에서 recommendation을 재실행해 중복 주문을 만들지 않습니다.
- `git reset --hard`, 자동 rollback, volume 삭제로 운영 상태를 임의 복구하지 않습니다.
- Policy deployment와 activation을 같은 작업으로 취급하지 않습니다.
