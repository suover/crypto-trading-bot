#!/usr/bin/env bash
set -euo pipefail

STRICT_LIVE=false
if (( $# > 1 )); then
  echo "사용법: bash scripts/check_server_runtime_safety.sh [--strict-live]" >&2
  exit 1
fi

if (( $# == 1 )); then
  case "$1" in
    --strict-live)
      STRICT_LIVE=true
      ;;
    *)
      echo "알 수 없는 옵션입니다: $1" >&2
      echo "사용법: bash scripts/check_server_runtime_safety.sh [--strict-live]" >&2
      exit 1
      ;;
  esac
fi

TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

pass_check() {
  local message="$1"
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  PASSED_CHECKS=$((PASSED_CHECKS + 1))
  echo "[통과] ${message}"
}

fail_check() {
  local message="$1"
  TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
  FAILED_CHECKS=$((FAILED_CHECKS + 1))
  echo "[실패] ${message}"
}

warn() {
  local message="$1"
  echo "[주의] ${message}"
}

container_running() {
  local container_name="$1"
  [[ "$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null || true)" == "true" ]]
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

echo "서버 런타임 안전 점검을 시작합니다."
if [[ "$STRICT_LIVE" == "true" ]]; then
  echo "엄격 라이브 모드(--strict-live)로 점검합니다."
else
  echo "일반 모드로 점검합니다. 라이브 제한값 경고는 실패로 처리하지 않습니다."
fi
echo ""

# A. Project root check
[[ -f docker-compose.yml ]] && pass_check "docker-compose.yml 파일이 있습니다." || fail_check "docker-compose.yml 파일이 없습니다. 저장소 루트에서 실행하세요."
[[ -f .env ]] && pass_check ".env 파일이 있습니다." || fail_check ".env 파일이 없습니다."
[[ -f scripts/backup_db.sh ]] && pass_check "scripts/backup_db.sh 파일이 있습니다." || fail_check "scripts/backup_db.sh 파일이 없습니다."

# B. .env permission check
if [[ -f .env ]]; then
  ENV_PERM="$(stat -c "%a" .env 2>/dev/null || echo "unknown")"
  if [[ "$ENV_PERM" == "600" ]]; then
    pass_check ".env 권한이 600입니다."
  else
    fail_check ".env 권한이 ${ENV_PERM}입니다. owner read/write만 허용되는 600이어야 합니다."
  fi
else
  fail_check ".env 권한을 확인할 수 없습니다."
fi

# C. Docker Compose config check
if docker compose config >/dev/null 2>&1; then
  pass_check "docker compose config 검증이 성공했습니다."
else
  fail_check "docker compose config 검증이 실패했습니다."
fi

# D. PostgreSQL container status check
if container_running "crypto-trading-postgres"; then
  pass_check "PostgreSQL 컨테이너가 실행 중입니다."
else
  fail_check "PostgreSQL 컨테이너 crypto-trading-postgres가 실행 중이 아닙니다."
fi

# E. PostgreSQL public port exposure check
POSTGRES_PORTS="$(docker ps --filter "name=^/crypto-trading-postgres$" --format "{{.Ports}}" 2>/dev/null || true)"
if [[ -z "$POSTGRES_PORTS" ]]; then
  fail_check "PostgreSQL 포트 상태를 확인할 수 없습니다. 컨테이너 실행 상태를 확인하세요."
elif [[ "$POSTGRES_PORTS" == *"->5432/tcp"* ]]; then
  fail_check "PostgreSQL이 호스트 포트로 공개되어 있습니다: ${POSTGRES_PORTS}. 서버 런타임에서는 docker-compose.local.yml을 사용하면 안 됩니다."
elif [[ "$POSTGRES_PORTS" == *"5432/tcp"* ]]; then
  pass_check "PostgreSQL은 내부 포트만 표시됩니다: ${POSTGRES_PORTS}"
else
  fail_check "PostgreSQL 포트 표시가 예상과 다릅니다: ${POSTGRES_PORTS}"
fi

# F. Required service status check
if container_running "crypto-trading-telegram-listener"; then
  pass_check "telegram-listener 컨테이너가 실행 중입니다."
else
  fail_check "telegram-listener 컨테이너가 실행 중이 아닙니다."
fi

if container_running "crypto-trading-mock-order-retry-worker"; then
  pass_check "mock-order-retry-worker 컨테이너가 실행 중입니다."
else
  fail_check "mock-order-retry-worker 컨테이너가 실행 중이 아닙니다."
fi

# G. Scheduler OFF check
if container_running "crypto-trading-ai-trade-scheduler"; then
  fail_check "ai-trade-scheduler가 실행 중입니다. 제한적 라이브 테스트 전에는 scheduler가 꺼져 있어야 합니다."
else
  pass_check "ai-trade-scheduler는 실행 중이 아닙니다. Exited 상태 컨테이너가 남아 있는 것은 허용됩니다."
fi

# H. Backup file check
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
    if (( BAD_BACKUP_PERMS == 0 )); then
      pass_check "모든 DB 백업 파일 권한이 600입니다."
    else
      fail_check "권한이 열린 DB 백업 파일이 ${BAD_BACKUP_PERMS}개 있습니다."
    fi
  else
    fail_check "${BACKUP_DIR}에 crypto_trading_bot_*.sql 백업 파일이 없습니다."
  fi
else
  fail_check "백업 디렉터리가 없습니다: ${BACKUP_DIR}"
fi

# I/J. Current .env live mode summary and optional strict-live checks
ORDER_EXECUTION_MODE="$(get_env_value ORDER_EXECUTION_MODE)"
LIVE_ORDER_ENABLED="$(get_env_value LIVE_ORDER_ENABLED)"
MAX_ORDER_AMOUNT_KRW="$(get_env_value MAX_ORDER_AMOUNT_KRW)"
DAILY_MAX_ORDER_AMOUNT_KRW="$(get_env_value DAILY_MAX_ORDER_AMOUNT_KRW)"
ALLOWED_MARKETS="$(get_env_value ALLOWED_MARKETS)"
MARKET_UNIVERSE_MODE="$(get_env_value MARKET_UNIVERSE_MODE)"
LIVE_DYNAMIC_MARKET_ENABLED="$(get_env_value LIVE_DYNAMIC_MARKET_ENABLED)"
AI_ANALYSIS_SCHEDULER_ENABLED="$(get_env_value AI_ANALYSIS_SCHEDULER_ENABLED)"

echo ""
echo ".env 라이브 관련 요약(비밀값 제외):"
echo "- ORDER_EXECUTION_MODE=${ORDER_EXECUTION_MODE:-<unset>}"
echo "- LIVE_ORDER_ENABLED=${LIVE_ORDER_ENABLED:-<unset>}"
echo "- MAX_ORDER_AMOUNT_KRW=${MAX_ORDER_AMOUNT_KRW:-<unset>}"
echo "- DAILY_MAX_ORDER_AMOUNT_KRW=${DAILY_MAX_ORDER_AMOUNT_KRW:-<unset>}"
echo "- ALLOWED_MARKETS=${ALLOWED_MARKETS:-<unset>}"
echo "- MARKET_UNIVERSE_MODE=${MARKET_UNIVERSE_MODE:-STATIC}"
echo "- LIVE_DYNAMIC_MARKET_ENABLED=${LIVE_DYNAMIC_MARKET_ENABLED:-false}"
echo "- AI_ANALYSIS_SCHEDULER_ENABLED=${AI_ANALYSIS_SCHEDULER_ENABLED:-<unset>}"
echo ""

LIVE_WARNING_COUNT=0

if [[ "$AI_ANALYSIS_SCHEDULER_ENABLED" == "true" ]]; then
  warn "AI_ANALYSIS_SCHEDULER_ENABLED=true 입니다. 제한적 라이브 테스트에서는 false가 권장됩니다."
  LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
fi

if [[ "${MARKET_UNIVERSE_MODE:-STATIC}" == "STATIC" && "$ALLOWED_MARKETS" == *,* ]]; then
  warn "ALLOWED_MARKETS에 여러 마켓이 있습니다: ${ALLOWED_MARKETS}. 제한적 라이브 테스트에서는 KRW-BTC 단일 마켓이 권장됩니다."
  LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
fi

if is_integer "$MAX_ORDER_AMOUNT_KRW" && (( MAX_ORDER_AMOUNT_KRW > 5000 )); then
  warn "MAX_ORDER_AMOUNT_KRW가 5000보다 큽니다: ${MAX_ORDER_AMOUNT_KRW}"
  LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
fi

if is_integer "$DAILY_MAX_ORDER_AMOUNT_KRW" && (( DAILY_MAX_ORDER_AMOUNT_KRW > 5000 )); then
  warn "DAILY_MAX_ORDER_AMOUNT_KRW가 5000보다 큽니다: ${DAILY_MAX_ORDER_AMOUNT_KRW}"
  LIVE_WARNING_COUNT=$((LIVE_WARNING_COUNT + 1))
fi

if [[ "$STRICT_LIVE" == "true" ]]; then
  [[ "$ORDER_EXECUTION_MODE" == "LIVE" ]] && pass_check "strict-live: ORDER_EXECUTION_MODE=LIVE입니다." || fail_check "strict-live: ORDER_EXECUTION_MODE이 LIVE가 아닙니다."
  [[ "$LIVE_ORDER_ENABLED" == "true" ]] && pass_check "strict-live: LIVE_ORDER_ENABLED=true입니다." || fail_check "strict-live: LIVE_ORDER_ENABLED가 true가 아닙니다."
  if is_integer "$MAX_ORDER_AMOUNT_KRW" && (( MAX_ORDER_AMOUNT_KRW <= 5000 )); then
    pass_check "strict-live: MAX_ORDER_AMOUNT_KRW가 5000 이하입니다."
  else
    fail_check "strict-live: MAX_ORDER_AMOUNT_KRW가 비어 있거나 5000보다 큽니다."
  fi
  if is_integer "$DAILY_MAX_ORDER_AMOUNT_KRW" && (( DAILY_MAX_ORDER_AMOUNT_KRW <= 5000 )); then
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
else
  if (( LIVE_WARNING_COUNT > 0 )); then
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
exit 0
