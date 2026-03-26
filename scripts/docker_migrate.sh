#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/docker_helpers.sh
source "${SCRIPT_DIR}/docker_helpers.sh"

echo "[docker_migrate] ensuring postgres is running"
compose up -d postgres

echo "[docker_migrate] applying migrations against ${POSTGRES_DATABASE_URL}"
compose run --rm -e DATABASE_URL="${POSTGRES_DATABASE_URL}" web flask db upgrade
