#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SHA=""
ORIGINAL_ARGUMENTS=("$@")
CURRENT_STAGE="argument validation"
PREVIOUS_COMMIT="unknown"
CURRENT_COMMIT="unknown"
SCHEDULER_RESTARTED=false

usage() {
  cat <<'EOF'
사용법: bash scripts/deploy_production.sh [--expected-sha <40-character SHA>]

Production main을 fast-forward 방식으로 배포합니다.
--expected-sha가 있으면 origin/main과 최종 HEAD가 해당 commit과 정확히 일치해야 합니다.
EOF
}

fail() {
  echo "오류: $1" >&2
  return 1
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

container_healthy() {
  [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || true)" == "healthy" ]]
}

on_error() {
  local exit_code=$?
  trap - ERR
  set +e
  if [[ "$SCHEDULER_RESTARTED" == "true" ]]; then
    docker compose --profile scheduler stop ai-trade-scheduler >/dev/null 2>&1
  fi
  CURRENT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  local scheduler_state="stopped"
  container_running "crypto-trading-ai-trade-scheduler" && scheduler_state="running"
  echo "" >&2
  echo "Production 배포가 실패했습니다." >&2
  echo "- failed_stage=${CURRENT_STAGE}" >&2
  echo "- previous_commit=${PREVIOUS_COMMIT}" >&2
  echo "- current_commit=${CURRENT_COMMIT}" >&2
  echo "- scheduler_state=${scheduler_state}" >&2
  echo "자동 Git/DB rollback은 수행하지 않았습니다." >&2
  echo "scheduler가 중지된 경우 사람이 상태를 확인하기 전에는 다시 시작하지 마세요." >&2
  exit "$exit_code"
}

while (( $# > 0 )); do
  case "$1" in
    --expected-sha)
      (( $# >= 2 )) || { usage >&2; exit 1; }
      EXPECTED_SHA="$2"
      shift 2
      ;;
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
done

if [[ -n "$EXPECTED_SHA" && ! "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  fail "--expected-sha는 40자리 Git commit SHA여야 합니다."
  exit 1
fi

# Continue from a temporary copy so a fast-forward cannot replace the script that
# the current Bash process is still reading. --help exits before this point.
if [[ "${DEPLOY_PRODUCTION_FROM_TEMP:-false}" != "true" ]]; then
  for bootstrap_command in bash cp chmod mktemp; do
    command -v "$bootstrap_command" >/dev/null 2>&1 || {
      fail "필수 명령을 찾을 수 없습니다: ${bootstrap_command}"
      exit 1
    }
  done
  DEPLOY_PRODUCTION_TEMP_FILE="$(mktemp)"
  cp -- "$0" "$DEPLOY_PRODUCTION_TEMP_FILE"
  chmod 700 "$DEPLOY_PRODUCTION_TEMP_FILE"
  export DEPLOY_PRODUCTION_FROM_TEMP=true
  export DEPLOY_PRODUCTION_TEMP_FILE
  exec bash "$DEPLOY_PRODUCTION_TEMP_FILE" "${ORIGINAL_ARGUMENTS[@]}"
fi

cleanup_temporary_script() {
  if [[ -n "${DEPLOY_PRODUCTION_TEMP_FILE:-}" ]]; then
    rm -f -- "$DEPLOY_PRODUCTION_TEMP_FILE"
  fi
}
trap cleanup_temporary_script EXIT

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$REPO_ROOT" ]] || { fail "Git 저장소 안에서 실행해야 합니다."; exit 1; }
cd "$REPO_ROOT"
[[ -f docker-compose.yml && -f scripts/backup_db.sh && -f .env ]] || {
  fail "저장소 루트에 docker-compose.yml, scripts/backup_db.sh, .env가 필요합니다."
  exit 1
}

CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "main" ]] || { fail "Production 배포는 main 브랜치에서만 가능합니다. current=${CURRENT_BRANCH}"; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { fail "working tree가 clean하지 않습니다. stash/reset 없이 배포를 중단합니다."; exit 1; }

for required_command in git docker bash stat cp chmod date mkdir mktemp rm; do
  command -v "$required_command" >/dev/null 2>&1 || {
    fail "필수 명령을 찾을 수 없습니다: ${required_command}"
    exit 1
  }
done
git remote get-url origin >/dev/null 2>&1 || { fail "origin remote가 없습니다."; exit 1; }
[[ "$(stat -c '%a' .env 2>/dev/null || echo unknown)" == "600" ]] || { fail ".env 권한은 600이어야 합니다."; exit 1; }

PREVIOUS_COMMIT="$(git rev-parse HEAD)"
CURRENT_COMMIT="$PREVIOUS_COMMIT"
export GIT_TERMINAL_PROMPT=0
trap on_error ERR

echo "Production 배포를 시작합니다."
echo "- previous_commit=${PREVIOUS_COMMIT}"
echo "- expected_sha=${EXPECTED_SHA:-<origin/main>}"

CURRENT_STAGE="Compose configuration validation"
docker compose config --quiet
docker compose --profile manual config --quiet
docker compose --profile scheduler config --quiet
docker compose --profile manual --profile scheduler config --quiet

CURRENT_STAGE="PostgreSQL preflight"
container_running "crypto-trading-postgres" || fail "PostgreSQL 컨테이너가 실행 중이 아닙니다."
container_healthy "crypto-trading-postgres" || fail "PostgreSQL 컨테이너가 healthy가 아닙니다."

CURRENT_STAGE="fetch and exact SHA validation"
git fetch origin main
ORIGIN_MAIN_SHA="$(git rev-parse refs/remotes/origin/main)"
if [[ -n "$EXPECTED_SHA" && "$ORIGIN_MAIN_SHA" != "$EXPECTED_SHA" ]]; then
  fail "origin/main이 expected SHA와 다릅니다. expected=${EXPECTED_SHA}, origin_main=${ORIGIN_MAIN_SHA}"
fi

CURRENT_STAGE="database backup"
BACKUP_DIR="${HOME}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ENV_BACKUP_FILE="${BACKUP_DIR}/crypto_trading_bot_env_${TIMESTAMP}.env"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
bash scripts/backup_db.sh

CURRENT_STAGE="environment backup"
cp -- .env "$ENV_BACKUP_FILE"
chmod 600 "$ENV_BACKUP_FILE"
echo ".env 백업을 생성했습니다: ${ENV_BACKUP_FILE}"

CURRENT_STAGE="scheduler stop"
if container_running "crypto-trading-ai-trade-scheduler"; then
  docker compose --profile scheduler stop ai-trade-scheduler
  echo "ai-trade-scheduler를 cutover 전에 중지했습니다."
else
  echo "ai-trade-scheduler는 이미 중지되어 있습니다."
fi

CURRENT_STAGE="fast-forward main update"
git merge --ff-only refs/remotes/origin/main
CURRENT_COMMIT="$(git rev-parse HEAD)"
[[ -z "$(git status --porcelain)" ]] || fail "Git 업데이트 후 working tree가 clean하지 않습니다."
if [[ -n "$EXPECTED_SHA" && "$CURRENT_COMMIT" != "$EXPECTED_SHA" ]]; then
  fail "최종 HEAD가 expected SHA와 다릅니다. expected=${EXPECTED_SHA}, head=${CURRENT_COMMIT}"
fi

CURRENT_STAGE="all application image build"
docker compose --profile manual --profile scheduler build \
  migrate \
  telegram-listener \
  mock-order-retry-worker \
  live-order-reconciliation-worker \
  ai-trade-analysis \
  ai-trade-scheduler

CURRENT_STAGE="application runtime stop"
docker compose stop telegram-listener mock-order-retry-worker live-order-reconciliation-worker
echo "telegram-listener와 retry/reconciliation worker를 migration 직전에 중지했습니다."

CURRENT_STAGE="database migration"
docker compose up \
  --no-deps \
  --force-recreate \
  --abort-on-container-exit \
  --exit-code-from migrate \
  migrate

CURRENT_STAGE="default runtime recreate"
docker compose up -d --no-deps --force-recreate \
  telegram-listener \
  mock-order-retry-worker \
  live-order-reconciliation-worker

CURRENT_STAGE="scheduler recreate"
docker compose --profile scheduler up -d --no-deps --force-recreate ai-trade-scheduler
SCHEDULER_RESTARTED=true

CURRENT_STAGE="post-deployment health check"
container_running "crypto-trading-postgres" || fail "PostgreSQL이 실행 중이 아닙니다."
container_healthy "crypto-trading-postgres" || fail "PostgreSQL이 healthy가 아닙니다."
[[ "$(docker inspect -f '{{.State.ExitCode}}' crypto-trading-migrate 2>/dev/null || echo missing)" == "0" ]] || fail "migrate 컨테이너가 정상 종료되지 않았습니다."
container_running "crypto-trading-telegram-listener" || fail "telegram-listener가 실행 중이 아닙니다."
container_running "crypto-trading-mock-order-retry-worker" || fail "mock-order-retry-worker가 실행 중이 아닙니다."
container_running "crypto-trading-live-order-reconciliation-worker" || fail "live-order-reconciliation-worker가 실행 중이 아닙니다."
container_running "crypto-trading-ai-trade-scheduler" || fail "ai-trade-scheduler가 실행 중이 아닙니다."

CURRENT_STAGE="production LIVE safety validation"
bash scripts/check_server_runtime_safety.sh --production-live

trap - ERR
CURRENT_COMMIT="$(git rev-parse HEAD)"
echo ""
echo "Production 배포가 완료되었습니다."
echo "- previous_commit=${PREVIOUS_COMMIT}"
echo "- deployed_commit=${CURRENT_COMMIT}"
echo "- postgres=healthy"
echo "- migrate=completed"
echo "- telegram-listener=running"
echo "- mock-order-retry-worker=running"
echo "- live-order-reconciliation-worker=running"
echo "- ai-trade-scheduler=running"
echo "deployment_success=true"
