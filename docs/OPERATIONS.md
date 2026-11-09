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
| Research Lifecycle | `RESEARCH_LIFECYCLE_ENABLED` | `false` | 없음, DB-only |

Flag를 바꾼 뒤에는 필요한 service만 명시적으로 recreate하고 logs와 safety check를 확인합니다. 설정 변경 전 `.env`를 권한 `600`으로 backup합니다. 이 문서는 실제 환경 값을 지정하지 않습니다.

Portfolio Performance worker는 PostgreSQL과 DB secret만 사용하지만 non-KRW completed external cash flow 평가에는 Upbit public candle을 조회할 수 있습니다. Upbit private API, OpenAI와 Telegram은 사용하지 않습니다.

### Research Lifecycle rollout

`research-lifecycle-worker`는 기본 runtime에 포함되지만 기본 설정에서는 DB 조회 없이 idle합니다. DB secret만 mount합니다. `RESEARCH_LIFECYCLE_ENABLED=true`만 설정하면 주기적 Gate 검증을 읽기 전용으로 실행하고, `RESEARCH_AUTO_SHADOW_ENROLLMENT_ENABLED=true`도 설정해야 조건부 Shadow 등록을 허용합니다. `.env`와 DB backup 후 먼저 Preview의 후보별 상태 및 무결성 오류를 확인합니다.

```bash
# repository root: DB-only Preview (Lifecycle flag와 무관하게 허용)
docker compose run --rm --no-deps research-lifecycle-worker python -m scripts.run_research_lifecycle_worker --once

# 두 flag가 모두 활성화된 경우에만 조건부 Apply
docker compose run --rm --no-deps research-lifecycle-worker python -m scripts.run_research_lifecycle_worker --once --apply

# 설정 변경 후 해당 worker만 recreate
docker compose up -d --no-deps --force-recreate research-lifecycle-worker
docker compose logs --tail=100 research-lifecycle-worker
```

상시 worker가 lock을 보유 중이면 one-shot은 중복 실행 없이 종료하므로 진단 시간에는 해당 worker만 중지합니다. 기본 주기는 21600초, 배치는 10개이며 `RESEARCH_LIFECYCLE_INTERVAL_SECONDS`와 `RESEARCH_LIFECYCLE_BATCH_SIZE`로 설정합니다. 구조화된 JSON은 전체 등록/검사 수, Gate 상태, 무결성·실행 오류, 기존/신규 enrollment, DB write 및 LIVE 영향(false)을 보고합니다. 상세 상태·순회·안전 계약은 [Policy Promotion](POLICY_PROMOTION.md#research-lifecycle-자동-검증과-조건부-등록)을 참고하세요. 기존 Shadow Selection 자동평가는 별도 flag로 활성화해야 합니다. 이 worker는 정책 승인이나 LIVE activation을 수행하지 않습니다.

## Research Candidate Automation Phase 2

`research-candidate-automation-worker`는 기본 runtime의 DB-only 서비스입니다. 자동 검토가 비활성화되어 있으면 DB에 연결하지 않고 대기합니다. 활성화 전 DB와 .env 백업, migration, `TRADING_USER_ID`, DYNAMIC Upbit KRW BASELINE 문맥, Replay Dataset과 Research Candidate Outcome 수집을 확인합니다.

1. `RESEARCH_CANDIDATE_AUTOMATION_ENABLED=true`만 켜고 Worker를 재생성합니다. 첫 정상 예약 실행은 최신 호환 스냅샷을 기준점으로 저장하고 `BOOTSTRAPPED`를 보고합니다. 기준점 이전의 데이터는 Historical Evidence에 사용할 수 있습니다.
2. 기본 86400초 주기에서 기준점 이후 신규 스냅샷 21개와 성숙 Outcome을 확인합니다. 결정적 생성 Step은 기본 0.05입니다. `NO_NEW_SNAPSHOTS`, `INSUFFICIENT_RESEARCH_DATA`, `NO_NOVEL_CANDIDATES`, `NO_PASS_CANDIDATES`는 정상 종료입니다.
3. 검토 결과를 확인한 뒤 자동 등록이 필요하면 `RESEARCH_CANDIDATE_AUTO_REGISTRATION_ENABLED=true`를 별도로 켜고 Worker를 재생성합니다. 이전 `PREVIEW_READY` 건은 새 Screening과 서명 검증을 거쳐 등록을 다시 판단합니다. 기본 상한은 주기당 5개, 동일 Context 전체 30개입니다.

```bash
docker compose run --rm --no-deps research-candidate-automation-worker python -m scripts.run_research_candidate_automation_worker --once
docker compose run --rm --no-deps research-candidate-automation-worker python -m scripts.run_research_candidate_automation_worker --once --apply
docker compose logs --tail=100 research-candidate-automation-worker
```

호스트에서는 `trading-engine/`에서 `uv run python -m scripts.run_research_candidate_automation_worker --once`로 실행합니다. 단발 Preview는 읽기 전용이며 최초 예약 관찰 전에는 `BOOTSTRAP_REQUIRED`를 보고합니다. `--once --apply`는 두 플래그와 초기 체크포인트를 요구합니다. `LOCK_NOT_ACQUIRED`는 다른 실행이 진행 중임을 뜻합니다.

JSON의 `state_write`, `registration_status`, `created_candidate_count`를 함께 확인하면 관찰·검토와 실제 등록을 구분할 수 있습니다. `REGISTRATION_CAP_EXCEEDED`는 전체 계획 보류입니다. 새 스냅샷이 충분해지거나 상한 설정이 달라지면 전체 계획을 다시 검증합니다. [연구 경계와 체크포인트](STRATEGY_RESEARCH.md#research-candidate-automation-phase-2)를 참고하세요.

## Shadow Review Monitoring Phase 3

배포 전 DB와 .env를 백업합니다. 배포 후 migration, `shadow-review-monitor-worker` 기동, `--production-live` 안전 점검을 확인합니다. 처음에는 두 플래그 모두 OFF로 두고 읽기 전용 Preview를 실행합니다. Compose 명령은 저장소 루트에서 실행합니다.

```bash
docker compose run --rm --no-deps shadow-review-monitor-worker python -m scripts.run_shadow_review_monitor_worker --once
docker compose run --rm --no-deps shadow-review-monitor-worker python -m scripts.run_shadow_review_monitor_worker --once --apply
docker compose logs --tail=100 shadow-review-monitor-worker
```

호스트에서는 `trading-engine/`에서 `uv run python -m scripts.run_shadow_review_monitor_worker --once`로 같은 Preview를 실행합니다. `--once`는 DB와 Telegram을 변경하지 않으며, 다음 배치를 평가해 Outbox 상태를 보고합니다. `LOCK_NOT_ACQUIRED`는 다른 주기가 실행 중이라는 뜻입니다.

1. `SHADOW_REVIEW_MONITOR_ENABLED=true`를 켜고 Worker를 재생성합니다. Telegram 플래그는 OFF로 유지합니다.
2. 두 번째 명령으로 단발 Apply를 실행하거나 예약 주기를 기다립니다. 유효한 숫자 `TELEGRAM_CHAT_ID`가 있으면 적격 미승인 후보가 `PENDING`으로 기록됩니다. 수신 대상이 비어 있으면 감시만 합니다.
3. Eligible 및 Outbox 집계를 확인하고 수신 대상과 Bot Token 설정을 검증합니다. 운영자의 별도 승인 후에만 `SHADOW_REVIEW_TELEGRAM_NOTIFICATIONS_ENABLED=true`로 전환하고 Worker를 재생성합니다.

기본 주기는 21600초, 배치는 10개, 주기별 Outbox 생성과 발송 시도 상한은 각각 5개입니다. 확실한 실패는 최초 시도 이후 최대 3회 재시도하며 최소 대기 시간은 1·5·15분입니다. 실제 재시도는 다음 Worker 주기에서 처리되므로 주기 설정만큼 늦어질 수 있습니다.

`UNCERTAIN`은 운영 확인 대상으로 보류합니다. 15분 이상 남은 `SENDING`은 다음 발송 주기에 `UNCERTAIN`으로 전환합니다. Telegram에서 수신 여부와 관련 로그를 확인하고, 상태를 임의로 `PENDING`으로 돌리지 않습니다. 아래 읽기 전용 조회는 토큰과 수신 대상 없이 점검할 수 있습니다.

```sql
SELECT id, candidate_id, shadow_enrollment_id, delivery_status,
       delivery_attempt_count, safe_failure_code, updated_at
FROM shadow_review_notifications
WHERE delivery_status IN ('FAILED', 'SENDING', 'UNCERTAIN', 'SUPPRESSED')
ORDER BY updated_at DESC
LIMIT 50;
```

Outbox를 삭제하거나 migration을 downgrade하면 중복 방지 이력도 사라집니다. 운영 변경 전 백업에서 이력을 복구할 수 있는지 확인합니다. [자격과 상태 전이](POLICY_PROMOTION.md#shadow-review-monitoring-phase-3)를 참고하세요.

## Telegram Policy Review Phase 4

CI 통과 후 DB와 .env 백업, migration `e8a0c2d4f6b1`, 기존 `telegram-listener`와 `shadow-review-monitor-worker` 기동, 기존 BUY/SELL Listener 및 `--production-live` 안전 점검을 확인합니다. 두 정책 플래그는 기본 OFF입니다. 정책 Callback은 기존 Listener의 단일 polling에서 분리 처리합니다.

1. 기존 `TELEGRAM_CHAT_ID`가 운영자의 봇 개인 채팅인지 확인합니다. 상세 근거는 원본 메시지에 표시되므로 그룹에서 Phase 4를 사용할 수 없습니다. 그룹 알림 운영에서는 Phase 4를 끈 상태로 Phase 3 평문 알림과 수동 CLI를 사용할 수 있습니다.
2. `POLICY_TELEGRAM_ALLOWED_USER_IDS`에 허용된 숫자 User ID를 쉼표로 구분해 등록합니다. 실제 개인 채팅 사용자 ID가 목록에 있어야 새 알림에 검토 버튼이 붙습니다.
3. `POLICY_TELEGRAM_REVIEW_ENABLED=true`, `POLICY_TELEGRAM_APPROVAL_ENABLED=false`로 상세 검토를 확인합니다. 설정 변경 후 두 기존 서비스를 재생성합니다. 새 Eligible 알림에서 버튼을 사용하며, 검증을 위해 Gate나 운영 데이터를 조작하지 않습니다.
4. 별도 운영자 확인 후 `POLICY_TELEGRAM_APPROVAL_ENABLED=true`를 켭니다. 이후 새 요청에서 상세 근거를 확인한 동일 관리자가 승인 또는 거절을 확정할 수 있습니다.

검토 토큰은 발송 receipt 저장부터 기본 7일, 확정 토큰은 상세 검토 저장부터 기본 15분입니다. 만료, 근거 변경, 수신 불확실 또는 상세 메시지 편집 실패로 확정 버튼을 사용할 수 없으면 최신 근거를 수동 CLI로 확인합니다. 기존 SENT 알림을 재발송하거나 토큰을 되살리지 않습니다. 승인 후 메시지 편집 실패는 승인 상태를 되돌리지 않습니다. 승인 ID와 요청 상태는 Listener의 구조화 로그 및 DB 감사 기록으로 확인합니다.

문제가 생기면 승인 플래그를 끄고 두 서비스를 재생성합니다. 스키마 downgrade는 저장된 Telegram 승인이 있으면 차단됩니다. 승인·감사 이력을 삭제해 우회하지 말고, 기존 활성 정책 종료는 별도 수동 절차를 사용합니다. [정책 승인·활성화·종료 절차](POLICY_PROMOTION.md#telegram-policy-review-phase-4)

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
