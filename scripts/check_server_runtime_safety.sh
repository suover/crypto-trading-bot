#!/usr/bin/env bash
set -euo pipefail

MODE="normal"

usage() {
  cat <<'EOF'
사용법: bash scripts/check_server_runtime_safety.sh [--strict-live|--production-live]

  옵션 없음           기존 일반 서버 점검
  --strict-live        첫 제한적 LIVE 주문용 5,000 KRW 엄격 점검
  --production-live    scheduled approval-based Production LIVE 점검
EOF
}

if (( $# > 1 )); then
  usage >&2
  exit 1
fi

if (( $# == 1 )); then
  case "$1" in
    --strict-live) MODE="strict-live" ;;
    --production-live) MODE="production-live" ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "알 수 없는 옵션입니다: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
fi

TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

pass_check() {
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  PASSED_CHECKS=$((PASSED_CHECKS + 1))
  echo "[통과] $1"
}

fail_check() {
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  FAILED_CHECKS=$((FAILED_CHECKS + 1))
  echo "[실패] $1"
}

warn() {
  echo "[주의] $1"
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

container_healthy() {
  [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || true)" == "healthy" ]]
}

get_env_value() {
  local key="$1"
  local line value
  line="$(grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -n 1 || true)"
  if [[ -z "$line" ]]; then
    echo ""
    return 0
  fi
  value="${line#*=}"
  value="${value%%#*}"
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  echo "$value" | xargs
}

is_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_positive_integer() {
  is_integer "$1" && (( 10#$1 > 0 ))
}

echo "서버 런타임 안전 점검을 시작합니다."
case "$MODE" in
  strict-live) echo "엄격 라이브 모드(--strict-live)로 점검합니다." ;;
  production-live) echo "Production scheduled LIVE 모드(--production-live)로 점검합니다." ;;
  *) echo "일반 모드로 점검합니다. 라이브 제한값 경고는 실패로 처리하지 않습니다." ;;
esac
echo ""

[[ -d .git && -f docker-compose.yml ]] && pass_check "저장소 루트와 docker-compose.yml을 확인했습니다." || fail_check "Git 저장소 루트에서 실행해야 하며 docker-compose.yml이 필요합니다."
[[ -f .env ]] && pass_check ".env 파일이 있습니다." || fail_check ".env 파일이 없습니다."
[[ -f scripts/backup_db.sh ]] && pass_check "scripts/backup_db.sh 파일이 있습니다." || fail_check "scripts/backup_db.sh 파일이 없습니다."

if [[ -f .env ]]; then
  ENV_PERM="$(stat -c "%a" .env 2>/dev/null || echo "unknown")"
  [[ "$ENV_PERM" == "600" ]] && pass_check ".env 권한이 600입니다." || fail_check ".env 권한이 ${ENV_PERM}입니다. 600이어야 합니다."
else
  fail_check ".env 권한을 확인할 수 없습니다."
fi

if [[ "$MODE" == "production-live" ]]; then
  docker compose --profile manual --profile scheduler config --quiet >/dev/null 2>&1 && pass_check "default/manual/scheduler Compose 구성이 유효합니다." || fail_check "default/manual/scheduler Compose 구성 검증이 실패했습니다."
else
  docker compose config --quiet >/dev/null 2>&1 && pass_check "docker compose config 검증이 성공했습니다." || fail_check "docker compose config 검증이 실패했습니다."
fi

container_running "crypto-trading-postgres" && pass_check "PostgreSQL 컨테이너가 실행 중입니다." || fail_check "PostgreSQL 컨테이너가 실행 중이 아닙니다."
if [[ "$MODE" == "production-live" ]]; then
  container_healthy "crypto-trading-postgres" && pass_check "PostgreSQL healthcheck가 healthy입니다." || fail_check "PostgreSQL healthcheck가 healthy가 아닙니다."
fi

POSTGRES_PORTS="$(docker ps --filter "name=^/crypto-trading-postgres$" --format "{{.Ports}}" 2>/dev/null || true)"
if [[ -z "$POSTGRES_PORTS" ]]; then
  fail_check "PostgreSQL 포트 상태를 확인할 수 없습니다."
elif [[ "$POSTGRES_PORTS" == *"->5432/tcp"* ]]; then
  fail_check "PostgreSQL이 호스트 포트로 공개되어 있습니다: ${POSTGRES_PORTS}"
elif [[ "$POSTGRES_PORTS" == *"5432/tcp"* ]]; then
  pass_check "PostgreSQL은 Docker 내부 포트만 사용합니다."
else
  fail_check "PostgreSQL 포트 표시가 예상과 다릅니다: ${POSTGRES_PORTS}"
fi

container_running "crypto-trading-telegram-listener" && pass_check "telegram-listener가 실행 중입니다." || fail_check "telegram-listener가 실행 중이 아닙니다."
container_running "crypto-trading-mock-order-retry-worker" && pass_check "mock-order-retry-worker가 실행 중입니다." || fail_check "mock-order-retry-worker가 실행 중이 아닙니다."

if [[ "$MODE" == "production-live" ]]; then
  container_running "crypto-trading-ai-trade-scheduler" && pass_check "ai-trade-scheduler가 실행 중입니다." || fail_check "Production LIVE에서는 ai-trade-scheduler가 실행 중이어야 합니다."
  container_running "crypto-trading-live-order-reconciliation-worker" && pass_check "live-order-reconciliation-worker가 실행 중입니다." || fail_check "Production LIVE에서는 live-order-reconciliation-worker가 실행 중이어야 합니다."
  LIVE_ORDER_RECONCILIATION_ENABLED="$(get_env_value LIVE_ORDER_RECONCILIATION_ENABLED)"
  [[ "${LIVE_ORDER_RECONCILIATION_ENABLED:-true}" == "true" ]] && pass_check "production-live: reconciliation 설정이 활성화되어 있습니다." || fail_check "production-live: LIVE_ORDER_RECONCILIATION_ENABLED가 true가 아닙니다."
elif container_running "crypto-trading-ai-trade-scheduler"; then
  fail_check "ai-trade-scheduler가 실행 중입니다. 제한적 LIVE 점검에서는 꺼져 있어야 합니다."
else
  pass_check "ai-trade-scheduler는 실행 중이 아닙니다."
fi

BACKUP_DIR="${HOME}/backups"
if [[ -d "$BACKUP_DIR" ]]; then
  pass_check "백업 디렉터리가 있습니다: ${BACKUP_DIR}"
  mapfile -t BACKUP_FILES < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'crypto_trading_bot_*.sql' | sort)
  if (( ${#BACKUP_FILES[@]} > 0 )); then
    pass_check "DB 백업 파일이 ${#BACKUP_FILES[@]}개 있습니다."
    BAD_BACKUP_PERMS=0
    for backup_file in "${BACKUP_FILES[@]}"; do
      backup_perm="$(stat -c "%a" "$backup_file" 2>/dev/null || echo "unknown")"
      if [[ "$backup_perm" != "600" ]]; then
        warn "백업 파일 권한이 600이 아닙니다: ${backup_file} (${backup_perm})"
        BAD_BACKUP_PERMS=$((BAD_BACKUP_PERMS + 1))
      fi
    done
    (( BAD_BACKUP_PERMS == 0 )) && pass_check "모든 DB 백업 파일 권한이 600입니다." || fail_check "권한이 열린 DB 백업 파일이 ${BAD_BACKUP_PERMS}개 있습니다."
  else
    fail_check "${BACKUP_DIR}에 DB 백업 파일이 없습니다."
  fi
else
  fail_check "백업 디렉터리가 없습니다: ${BACKUP_DIR}"
fi

TRADING_MODE="$(get_env_value TRADING_MODE)"
ORDER_EXECUTION_MODE="$(get_env_value ORDER_EXECUTION_MODE)"
LIVE_ORDER_ENABLED="$(get_env_value LIVE_ORDER_ENABLED)"
LIVE_ORDER_CONFIRMATION="$(get_env_value LIVE_ORDER_CONFIRMATION)"
MAX_ORDER_AMOUNT_KRW="$(get_env_value MAX_ORDER_AMOUNT_KRW)"
DAILY_MAX_ORDER_AMOUNT_KRW="$(get_env_value DAILY_MAX_ORDER_AMOUNT_KRW)"
ALLOWED_MARKETS="$(get_env_value ALLOWED_MARKETS)"
MARKET_UNIVERSE_MODE="$(get_env_value MARKET_UNIVERSE_MODE)"
LIVE_DYNAMIC_MARKET_ENABLED="$(get_env_value LIVE_DYNAMIC_MARKET_ENABLED)"
AI_ANALYSIS_SCHEDULER_ENABLED="$(get_env_value AI_ANALYSIS_SCHEDULER_ENABLED)"
AI_ANALYSIS_SCHEDULE_TIMES="$(get_env_value AI_ANALYSIS_SCHEDULE_TIMES)"
SECRET_DIR="$(get_env_value SECRET_DIR)"

echo ""
echo ".env 라이브 관련 요약(비밀값 제외):"
echo "- TRADING_MODE=${TRADING_MODE:-<unset>}"
echo "- ORDER_EXECUTION_MODE=${ORDER_EXECUTION_MODE:-<unset>}"
echo "- LIVE_ORDER_ENABLED=${LIVE_ORDER_ENABLED:-<unset>}"
echo "- MAX_ORDER_AMOUNT_KRW=${MAX_ORDER_AMOUNT_KRW:-<unset>}"
echo "- DAILY_MAX_ORDER_AMOUNT_KRW=${DAILY_MAX_ORDER_AMOUNT_KRW:-<unset>}"
echo "- ALLOWED_MARKETS=${ALLOWED_MARKETS:-<unset>}"
echo "- MARKET_UNIVERSE_MODE=${MARKET_UNIVERSE_MODE:-STATIC}"
echo "- LIVE_DYNAMIC_MARKET_ENABLED=${LIVE_DYNAMIC_MARKET_ENABLED:-false}"
echo "- AI_ANALYSIS_SCHEDULER_ENABLED=${AI_ANALYSIS_SCHEDULER_ENABLED:-<unset>}"
echo "- AI_ANALYSIS_SCHEDULE_TIMES=${AI_ANALYSIS_SCHEDULE_TIMES:-<unset>}"
echo ""

if [[ "$MODE" == "production-live" ]]; then
  if [[ -n "$SECRET_DIR" && -d "$SECRET_DIR" ]]; then
    pass_check "SECRET_DIR이 설정되어 있고 디렉터리가 존재합니다."
    SECRET_DIR_PERM="$(stat -c "%a" "$SECRET_DIR" 2>/dev/null || echo "unknown")"
    [[ "$SECRET_DIR_PERM" == "700" ]] && pass_check "SECRET_DIR 권한이 700입니다." || fail_check "SECRET_DIR 권한이 ${SECRET_DIR_PERM}입니다. 700이어야 합니다."
    REQUIRED_SECRET_FILES=(postgres_password openai_api_key telegram_bot_token upbit_access_key upbit_secret_key)
    BAD_SECRET_FILES=0
    for secret_name in "${REQUIRED_SECRET_FILES[@]}"; do
      secret_file="${SECRET_DIR}/${secret_name}"
      if [[ ! -s "$secret_file" ]]; then
        warn "필수 secret 파일이 없거나 비어 있습니다: ${secret_file}"
        BAD_SECRET_FILES=$((BAD_SECRET_FILES + 1))
        continue
      fi
      secret_perm="$(stat -c "%a" "$secret_file" 2>/dev/null || echo "unknown")"
      if [[ "$secret_perm" != "600" ]]; then
        warn "secret 파일 권한이 600이 아닙니다: ${secret_file} (${secret_perm})"
        BAD_SECRET_FILES=$((BAD_SECRET_FILES + 1))
      fi
    done
    (( BAD_SECRET_FILES == 0 )) && pass_check "필수 secret 파일과 권한을 확인했습니다." || fail_check "필수 secret 파일 또는 권한 문제 ${BAD_SECRET_FILES}건이 있습니다."
  else
    fail_check "SECRET_DIR이 비어 있거나 디렉터리가 존재하지 않습니다."
  fi

  [[ "$TRADING_MODE" == "AI_APPROVAL" ]] && pass_check "production-live: TRADING_MODE=AI_APPROVAL입니다." || fail_check "production-live: TRADING_MODE이 AI_APPROVAL이 아닙니다."
  [[ "$ORDER_EXECUTION_MODE" == "LIVE" ]] && pass_check "production-live: ORDER_EXECUTION_MODE=LIVE입니다." || fail_check "production-live: ORDER_EXECUTION_MODE이 LIVE가 아닙니다."
  [[ "$LIVE_ORDER_ENABLED" == "true" ]] && pass_check "production-live: LIVE_ORDER_ENABLED=true입니다." || fail_check "production-live: LIVE_ORDER_ENABLED가 true가 아닙니다."
  [[ "$LIVE_ORDER_CONFIRMATION" == "ENABLE_LIVE_UPBIT_ORDERS" ]] && pass_check "production-live: LIVE 주문 confirmation이 유효합니다." || fail_check "production-live: LIVE 주문 confirmation이 유효하지 않습니다."
  if [[ "${MARKET_UNIVERSE_MODE:-STATIC}" == "DYNAMIC" ]]; then
    [[ "$LIVE_DYNAMIC_MARKET_ENABLED" == "true" ]] && pass_check "production-live: DYNAMIC LIVE 명시 플래그가 켜져 있습니다." || fail_check "production-live: DYNAMIC 모드에서 LIVE_DYNAMIC_MARKET_ENABLED=true가 아닙니다."
  fi
  [[ "$AI_ANALYSIS_SCHEDULER_ENABLED" == "true" ]] && pass_check "production-live: scheduler 설정이 활성화되어 있습니다." || fail_check "production-live: AI_ANALYSIS_SCHEDULER_ENABLED=true가 아닙니다."
  [[ -n "$AI_ANALYSIS_SCHEDULE_TIMES" ]] && pass_check "production-live: scheduler 실행 시각이 설정되어 있습니다." || fail_check "production-live: AI_ANALYSIS_SCHEDULE_TIMES가 비어 있습니다."
  is_positive_integer "$MAX_ORDER_AMOUNT_KRW" && pass_check "production-live: MAX_ORDER_AMOUNT_KRW가 유효한 양수입니다." || fail_check "production-live: MAX_ORDER_AMOUNT_KRW가 유효한 양의 정수가 아닙니다."
  is_positive_integer "$DAILY_MAX_ORDER_AMOUNT_KRW" && pass_check "production-live: DAILY_MAX_ORDER_AMOUNT_KRW가 유효한 양수입니다." || fail_check "production-live: DAILY_MAX_ORDER_AMOUNT_KRW가 유효한 양의 정수가 아닙니다."
  if is_positive_integer "$MAX_ORDER_AMOUNT_KRW" && is_positive_integer "$DAILY_MAX_ORDER_AMOUNT_KRW"; then
    if (( 10#$DAILY_MAX_ORDER_AMOUNT_KRW < 10#$MAX_ORDER_AMOUNT_KRW )); then
      warn "production-live: 일일 BUY 한도가 건별 BUY 한도보다 작습니다. 더 보수적인 유효한 안전 정책입니다."
    fi
  fi
else
  LIVE_WARNING_COUNT=0
  if [[ "$AI_ANALYSIS_SCHEDULER_ENABLED" == "true" ]]; then
    warn "AI_ANALYSIS_SCHEDULER_ENABLED=true 입니다. 제한적 라이브 테스트에서는 false가 권장됩니다."
    LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
  fi
  if [[ "${MARKET_UNIVERSE_MODE:-STATIC}" == "STATIC" && "$ALLOWED_MARKETS" == *,* ]]; then
    warn "ALLOWED_MARKETS에 여러 마켓이 있습니다: ${ALLOWED_MARKETS}. 제한적 라이브 테스트에서는 KRW-BTC 단일 마켓이 권장됩니다."
    LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
  fi
  if is_integer "$MAX_ORDER_AMOUNT_KRW" && (( 10#$MAX_ORDER_AMOUNT_KRW > 5000 )); then
    warn "MAX_ORDER_AMOUNT_KRW가 5000보다 큽니다: ${MAX_ORDER_AMOUNT_KRW}"
    LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
  fi
  if is_integer "$DAILY_MAX_ORDER_AMOUNT_KRW" && (( 10#$DAILY_MAX_ORDER_AMOUNT_KRW > 5000 )); then
    warn "DAILY_MAX_ORDER_AMOUNT_KRW가 5000보다 큽니다: ${DAILY_MAX_ORDER_AMOUNT_KRW}"
    LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
  fi

  if [[ "$MODE" == "strict-live" ]]; then
    [[ "$ORDER_EXECUTION_MODE" == "LIVE" ]] && pass_check "strict-live: ORDER_EXECUTION_MODE=LIVE입니다." || fail_check "strict-live: ORDER_EXECUTION_MODE이 LIVE가 아닙니다."
    [[ "$LIVE_ORDER_ENABLED" == "true" ]] && pass_check "strict-live: LIVE_ORDER_ENABLED=true입니다." || fail_check "strict-live: LIVE_ORDER_ENABLED가 true가 아닙니다."
    if is_integer "$MAX_ORDER_AMOUNT_KRW" && (( 10#$MAX_ORDER_AMOUNT_KRW <= 5000 )); then
      pass_check "strict-live: MAX_ORDER_AMOUNT_KRW가 5000 이하입니다."
    else
      fail_check "strict-live: MAX_ORDER_AMOUNT_KRW가 비어 있거나 5000보다 큽니다."
    fi
    if is_integer "$DAILY_MAX_ORDER_AMOUNT_KRW" && (( 10#$DAILY_MAX_ORDER_AMOUNT_KRW <= 5000 )); then
      pass_check "strict-live: DAILY_MAX_ORDER_AMOUNT_KRW가 5000 이하입니다."
    else
      fail_check "strict-live: DAILY_MAX_ORDER_AMOUNT_KRW가 비어 있거나 5000보다 큽니다."
    fi
    if [[ "${MARKET_UNIVERSE_MODE:-STATIC}" == "DYNAMIC" ]]; then
      [[ "$LIVE_DYNAMIC_MARKET_ENABLED" == "true" ]] && pass_check "strict-live: DYNAMIC LIVE 명시 플래그가 켜져 있습니다." || fail_check "strict-live: LIVE_DYNAMIC_MARKET_ENABLED=true가 아닙니다."
    else
      [[ "$ALLOWED_MARKETS" == "KRW-BTC" ]] && pass_check "strict-live: STATIC ALLOWED_MARKETS=KRW-BTC입니다." || fail_check "strict-live: STATIC ALLOWED_MARKETS가 정확히 KRW-BTC가 아닙니다."
    fi
    [[ "$AI_ANALYSIS_SCHEDULER_ENABLED" == "false" ]] && pass_check "strict-live: AI_ANALYSIS_SCHEDULER_ENABLED=false입니다." || fail_check "strict-live: AI_ANALYSIS_SCHEDULER_ENABLED가 false가 아닙니다."
  elif (( LIVE_WARNING_COUNT > 0 )); then
    warn "일반 모드에서는 라이브 제한값 경고 ${LIVE_WARNING_COUNT}개를 실패로 처리하지 않습니다. 제한적 라이브 전에는 --strict-live로 다시 확인하세요."
  else
    pass_check ".env 라이브 제한값 경고가 없습니다."
  fi
fi

echo ""
echo "점검 요약: 총 ${TOTAL_CHECKS}개, 통과 ${PASSED_CHECKS}개, 실패 ${FAILED_CHECKS}개"
if (( FAILED_CHECKS > 0 )); then
  echo "서버 런타임 안전 점검에 실패했습니다." >&2
  exit 1
fi
echo "서버 런타임 안전 점검을 통과했습니다."
