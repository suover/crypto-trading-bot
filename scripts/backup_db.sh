#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${HOME}/backups"
BACKUP_KEEP_COUNT="${BACKUP_KEEP_COUNT:-10}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/crypto_trading_bot_${TIMESTAMP}.sql"
CONTAINER_NAME="crypto-trading-postgres"
DB_USER="trading_user"
DB_NAME="crypto_trading_bot"

if ! [[ "$BACKUP_KEEP_COUNT" =~ ^[0-9]+$ ]] || (( BACKUP_KEEP_COUNT < 1 )); then
  echo "오류: BACKUP_KEEP_COUNT는 1 이상의 정수여야 합니다." >&2
  exit 1
fi

echo "PostgreSQL DB 백업을 시작합니다."

if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" != "true" ]]; then
  echo "오류: PostgreSQL 컨테이너가 실행 중이 아닙니다: ${CONTAINER_NAME}" >&2
  echo "먼저 서버 런타임 상태를 확인하세요: docker compose ps -a" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "백업 파일 경로: ${BACKUP_FILE}"
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"

chmod 600 "$BACKUP_FILE"
echo "백업 파일 권한을 600으로 설정했습니다."

mapfile -t BACKUPS < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'crypto_trading_bot_*.sql' | sort -r)

DELETED_COUNT=0
if (( ${#BACKUPS[@]} > BACKUP_KEEP_COUNT )); then
  for OLD_BACKUP in "${BACKUPS[@]:BACKUP_KEEP_COUNT}"; do
    rm -f -- "$OLD_BACKUP"
    ((DELETED_COUNT += 1))
  done
fi

echo "오래된 백업 정리 완료: ${DELETED_COUNT}개 삭제, 최근 ${BACKUP_KEEP_COUNT}개 유지"
echo "DB 백업이 완료되었습니다: ${BACKUP_FILE}"
