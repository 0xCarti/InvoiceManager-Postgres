#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

cat >"${tmp_dir}/.env" <<'EOF'
DATABASE_USER=test_user
DATABASE_PASSWORD=test_password
DATABASE_PORT=6543
DATABASE_NAME=test_db
EOF

expected_url="postgresql+psycopg://test_user:test_password@postgres:6543/test_db"
actual_url="$(
    cd "${tmp_dir}"
    # shellcheck source=scripts/docker_helpers.sh
    source "${REPO_ROOT}/scripts/docker_helpers.sh"
    printf '%s' "${POSTGRES_DATABASE_URL}"
)"

if [ "${actual_url}" != "${expected_url}" ]; then
    echo "[check_docker_migrate_env_fallback] expected '${expected_url}' but got '${actual_url}'" >&2
    exit 1
fi

echo "[check_docker_migrate_env_fallback] PASS: DATABASE_* fallback produced ${actual_url}"
