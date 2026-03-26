#!/bin/sh
set -e

DB_READY_TIMEOUT="${DB_READY_TIMEOUT:-60}"
DB_READY_INTERVAL="${DB_READY_INTERVAL:-2}"

log() {
    printf '%s %s\n' "[entrypoint]" "$*"
}

wait_for_database() {
    log "Waiting for Postgres readiness before running migrations..."

    python <<'PYTHON'
import os
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import psycopg

conninfo = os.environ.get("DATABASE_URL")
if conninfo and conninfo.startswith("postgresql+"):
    parsed = urlsplit(conninfo)
    normalized_scheme = "postgresql"
    conninfo = urlunsplit((normalized_scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    print(
        f"[entrypoint] Normalized DATABASE_URL scheme from '{parsed.scheme}' to '{normalized_scheme}' for readiness probe.",
        flush=True,
    )
if not conninfo:
    user = os.environ.get("DATABASE_USER", "invoicemanager")
    password = os.environ.get("DATABASE_PASSWORD", "invoicemanager")
    host = os.environ.get("DATABASE_HOST", "postgres")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("DATABASE_NAME", "invoicemanager")
    conninfo = f"postgresql://{user}:{password}@{host}:{port}/{name}"

interval = float(os.environ.get("DB_READY_INTERVAL", "2"))
timeout = float(os.environ.get("DB_READY_TIMEOUT", "60"))
deadline = time.monotonic() + timeout
attempt = 0

while True:
    attempt += 1
    try:
        with psycopg.connect(conninfo=conninfo, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        print(f"[entrypoint] Database is ready after {attempt} attempt(s).", flush=True)
        sys.exit(0)
    except Exception as exc:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                f"[entrypoint] Database readiness check failed after {attempt} attempt(s): {exc}",
                flush=True,
            )
            sys.exit(1)
        print(
            f"[entrypoint] Database not ready yet (attempt {attempt}): {exc}. Retrying in {interval:.1f}s...",
            flush=True,
        )
        time.sleep(interval)
PYTHON
}

wait_for_database

log "Running database migrations..."
flask db upgrade

log "Checking initial seed data..."
python <<'PYTHON'
from seed_data import seed_initial_data
from app import create_app
from app.models import User, Setting

app, _ = create_app([])
with app.app_context():
    needs_seed = (
        User.query.filter_by(is_admin=True).first() is None
        or Setting.query.filter_by(name="GST").first() is None
        or Setting.query.filter_by(name="DEFAULT_TIMEZONE").first() is None
    )
    if needs_seed:
        print("[entrypoint] Seed data missing; running initial seed.", flush=True)
        seed_initial_data()
    else:
        print("[entrypoint] Seed data already present; skipping.", flush=True)
PYTHON

if [ "$1" = "gunicorn" ]; then
    shift
    log "Starting gunicorn..."
    exec gunicorn "$@"
fi

log "Starting command: $*"
exec "$@"
