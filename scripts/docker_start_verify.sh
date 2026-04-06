#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/docker_helpers.sh
source "${SCRIPT_DIR}/docker_helpers.sh"

PORT_VALUE="${PORT:-5000}"
HEALTH_RETRIES="${HEALTH_RETRIES:-15}"
HEALTH_DELAY_SECONDS="${HEALTH_DELAY_SECONDS:-2}"

echo "[docker_start_verify] 1) start services"
compose up -d postgres redis

echo "[docker_start_verify] 2) build web image"
compose build web

echo "[docker_start_verify] 3) run migrations"
compose run --rm -e DATABASE_URL="${POSTGRES_DATABASE_URL}" web flask db upgrade

echo "[docker_start_verify] 4) start web"
compose up -d web

echo "[docker_start_verify] 5) verify containers and health endpoint"
compose ps
attempt=1
while [ "${attempt}" -le "${HEALTH_RETRIES}" ]; do
    if curl --fail --silent --show-error "http://localhost:${PORT_VALUE}/" >/dev/null; then
        break
    fi
    if [ "${attempt}" -eq "${HEALTH_RETRIES}" ]; then
        echo "[docker_start_verify] warning: / check failed after ${HEALTH_RETRIES} attempts. Verify app logs with 'docker compose logs web'."
        break
    fi
    sleep "${HEALTH_DELAY_SECONDS}"
    attempt=$((attempt + 1))
done
