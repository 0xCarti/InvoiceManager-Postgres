#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
    # shellcheck disable=SC1091
    source .env
fi

DATABASE_DRIVER="${DATABASE_DRIVER:-postgresql+psycopg}"
DATABASE_USER="${DATABASE_USER:-invoicemanager}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-invoicemanager}"
DATABASE_PORT="${DATABASE_PORT:-5432}"
DATABASE_NAME="${DATABASE_NAME:-invoicemanager}"

POSTGRES_DATABASE_URL="${DATABASE_URL:-${DATABASE_DRIVER}://${DATABASE_USER}:${DATABASE_PASSWORD}@postgres:${DATABASE_PORT}/${DATABASE_NAME}}"

compose() {
    docker compose "$@"
}
