#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/docker_helpers.sh
source "${SCRIPT_DIR}/docker_helpers.sh"

PORT_VALUE="${PORT:-5000}"

echo "[docker_start_verify] 1) start services"
compose up -d postgres

echo "[docker_start_verify] 2) run migrations"
compose run --rm -e DATABASE_URL="${POSTGRES_DATABASE_URL}" web flask db upgrade

echo "[docker_start_verify] 3) start web"
compose up -d web

echo "[docker_start_verify] 4) verify containers and health endpoint"
compose ps
curl --fail --silent --show-error "http://localhost:${PORT_VALUE}/" >/dev/null || {
    echo "[docker_start_verify] warning: / check failed. Verify app logs with 'docker compose logs web'."
}
