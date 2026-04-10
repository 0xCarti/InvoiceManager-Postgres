# Invoice Manager
[![codecov](https://codecov.io/github/0xCarti/InvoiceManager/branch/main/graph/badge.svg?token=GDFIVY6JX6)](https://codecov.io/github/0xCarti/InvoiceManager)
[![Build status](https://github.com/0xCarti/InvoiceManager/actions/workflows/build-main.yml/badge.svg?branch=main)](https://github.com/0xCarti/InvoiceManager/actions/workflows/build-main.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Flask-based application for managing invoices, products and vendors. The project comes with a comprehensive test suite using `pytest`.

PostgreSQL is the only supported runtime database backend.

## Table of Contents

- [Quick Gotchas](#quick-gotchas)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Database Setup & Migrations](#database-setup--migrations)
- [Run the Application](#run-the-application)
- [Backups & Restore (Postgres Runtime)](#backups--restore-postgres-runtime)
- [Project Architecture](#project-architecture)
- [Docker Setup](#docker-setup)
- [Command Reference](#command-reference)
- [Setup Paths](#setup-paths)
- [Testing](#testing)
- [Code Style](#code-style)
- [Features](#features)
- [Documentation](#documentation)
- [Data Import](#data-import)
- [Test Defaults](#test-defaults)
- [Documentation Index](#documentation-index)
- [License](#license)


## Quick Gotchas

- **POS email imports are deny-by-default:** configure `MAILGUN_ALLOWED_SENDERS` or `MAILGUN_ALLOWED_SENDER_DOMAINS` before enabling webhook or poll-mode ingestion.
- **Uploads are capped:** oversized uploads are rejected using `MAX_UPLOAD_FILE_SIZE_BYTES` and `POS_IMPORT_MAX_ATTACHMENT_BYTES`.
- ⚠️ **Docker Compose DB host:** when app/tools run **inside Compose services**, set `DATABASE_HOST=postgres` (service DNS name), not `localhost`.
- ⚠️ **Host-run tools:** when running commands from your host shell/venv (for example `flask db upgrade`), use `localhost` for DB host/URL unless you are targeting another host.
- ⚠️ **Connection var precedence:** `DATABASE_URL` (and `SQLALCHEMY_DATABASE_URI`) override individual `DATABASE_*` parts when set.
- ⚠️ **Restore prerequisite:** run migrations to latest **before** restoring a backup (`python -m flask --app run.py db upgrade` or `./scripts/docker_migrate.sh`).

Additional production note:

- Set `SKIP_DB_CREATE_ALL=true` for host deployments that rely on Alembic migrations. The Docker image already sets this internally, and `FLASK_SKIP_CREATE_ALL` remains supported as a legacy alias.

## Installation

You can perform the steps below manually or run one of the setup scripts provided in the repository. `setup.sh` works on Linux/macOS and `setup.ps1` works on Windows. Each script optionally accepts a repository URL and target directory, clones the project, installs dependencies, prepares a `.env` file, runs the database migrations, and seeds the default admin account and settings.


1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd InvoiceManager
   ```
2. **Create a virtual environment** (recommended)
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   This installs Flask-SQLAlchemy plus the PostgreSQL driver
   `psycopg[binary]`, which is the default runtime database adapter.

## Environment Variables

The application requires several variables to be present in your environment:

```env
SECRET_KEY=replace-with-a-long-random-value
ADMIN_EMAIL=admin@example.com
ADMIN_PASS=replace-with-a-long-random-admin-password
PORT=5000

DATABASE_DRIVER=postgresql+psycopg
DATABASE_HOST=postgres
DATABASE_PORT=5432
HOST_DATABASE_PORT=5432
DATABASE_USER=invoice_manager
DATABASE_PASSWORD=replace-with-a-strong-db-password
DATABASE_NAME=invoicemanager
DATABASE_URL=postgresql+psycopg://invoice_manager:replace-with-a-strong-db-password@postgres:5432/invoicemanager
SQLALCHEMY_DATABASE_URI=postgresql+psycopg://invoice_manager:replace-with-a-strong-db-password@postgres:5432/invoicemanager

SQLALCHEMY_POOL_PRE_PING=true
SQLALCHEMY_POOL_RECYCLE=1800
SQLALCHEMY_POOL_TIMEOUT=30
SQLALCHEMY_POOL_SIZE=5
SQLALCHEMY_MAX_OVERFLOW=10
SQLALCHEMY_POOL_USE_LIFO=true
MAX_UPLOAD_FILE_SIZE_BYTES=10485760
POS_IMPORT_MAX_ATTACHMENT_BYTES=10485760

SMTP_HOST=smtp.example.com
SMTP_PORT=25
SMTP_USERNAME=mailer-user
SMTP_PASSWORD=mailer-pass
SMTP_SENDER=no-reply@example.com
SMTP_USE_TLS=true
SMTP_TIMEOUT_SECONDS=10

METABASE_PORT=3000
METABASE_SITE_URL=http://localhost:3000
RATELIMIT_STORAGE_URI=redis://redis:6379/0
MAILGUN_WEBHOOK_SIGNING_KEY=mailgun-signing-key
MAILGUN_ALLOWED_SENDERS=
MAILGUN_ALLOWED_SENDER_DOMAINS=example.com
POS_IMPORT_INGEST_MODE=webhook
```

| Variable(s) | Meaning / behavior |
| --- | --- |
| `SECRET_KEY` | Flask secret key used for sessions. |
| `ADMIN_EMAIL`, `ADMIN_PASS` | Initial administrator account credentials. |
| `PORT` | Web server port (optional, defaults to `5000`). |
| `DATABASE_DRIVER`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME` | Individual PostgreSQL connection components used to build the SQLAlchemy URI. `DATABASE_USER`, `DATABASE_PASSWORD`, and `DATABASE_NAME` are required unless you set `DATABASE_URL` or `SQLALCHEMY_DATABASE_URI` directly. |
| `HOST_DATABASE_PORT` | Host-side port published for the Postgres container in Docker Compose. Leave this at `5432` unless that port is already in use on your machine. |
| `DATABASE_URL`, `SQLALCHEMY_DATABASE_URI` | Optional full SQLAlchemy URI overrides. If either is set, it takes precedence over individual `DATABASE_*` values. |
| `SQLALCHEMY_POOL_PRE_PING` | Enables stale-connection checks before checkout (defaults to `true`). |
| `SQLALCHEMY_POOL_RECYCLE` | Recycles pooled connections after this many seconds (defaults to `1800`). |
| `SQLALCHEMY_POOL_TIMEOUT` | Seconds to wait for a pooled connection before timing out (defaults to `30`). |
| `SQLALCHEMY_POOL_SIZE` | Steady-state number of pooled connections per process (defaults to `5`). |
| `SQLALCHEMY_MAX_OVERFLOW` | Extra burst connections allowed above `SQLALCHEMY_POOL_SIZE` (defaults to `10`). |
| `SQLALCHEMY_POOL_USE_LIFO` | Uses LIFO checkout behavior to let older idle connections expire naturally in containerized environments (defaults to `true`). |
| `MAX_UPLOAD_FILE_SIZE_BYTES` | Global request-body upload cap. Oversized browser and webhook uploads are rejected before parsing (defaults to `10485760`, or 10 MB). |
| `POS_IMPORT_MAX_ATTACHMENT_BYTES` | Per-attachment cap for POS email imports in webhook and poll mode (defaults to `MAX_UPLOAD_FILE_SIZE_BYTES`). |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`, `SMTP_USE_TLS`, `SMTP_TIMEOUT_SECONDS` | SMTP settings for password reset and invitation emails (`SMTP_PORT` defaults to `25`; set `SMTP_USE_TLS=true` to enable TLS; `SMTP_TIMEOUT_SECONDS` defaults to `10`). |
| `METABASE_PORT`, `METABASE_SITE_URL` | `METABASE_PORT` controls the host port published by the Metabase Compose service. `METABASE_SITE_URL` is the browser-facing URL the dashboard's Metabase button redirects to, and it is also passed to Metabase as `MB_SITE_URL` so Metabase-generated links use the same public address. Do not set this to a Docker service/container name unless clients can actually resolve that name. |
| `RATELIMIT_STORAGE_URI` | URI for the rate limiting backend. Use a persistent store such as Redis in production (for example `redis://redis:6379/0`). |
| `MAILGUN_WEBHOOK_SIGNING_KEY` | Mailgun inbound signing key used to verify webhook authenticity. |
| `MAILGUN_ALLOWED_SENDERS`, `MAILGUN_ALLOWED_SENDER_DOMAINS` | Sender allowlists for POS imports. Configure at least one of these before enabling webhook or poll-mode ingestion. |
| `POS_IMPORT_INGEST_MODE` | POS import ingestion strategy: `webhook` (default) for Mailgun inbound webhooks, or `poll` for scheduled mailbox-provider ingestion. |

A persistent backing store is required for rate limiting in production. Set
`RATELIMIT_STORAGE_URI` to a supported service so that limits are shared
across workers.

These SMTP variables enable password reset and user invitation emails. Configure them in your `.env` file if you want users to receive those messages.

The GST number can now be set from the application control panel after installation.

These can be placed in a `.env` file or exported in your shell before starting the app.

### Optional Environment Variables

- At least one of `MAILGUN_ALLOWED_SENDERS` or `MAILGUN_ALLOWED_SENDER_DOMAINS` must be configured before POS email ingestion is enabled.
- `SESSION_COOKIE_SECURE` – set to `false` when running over plain HTTP (for
  example in local development). Defaults to `true` so cookies are only sent
  over HTTPS in production.
- `ENFORCE_HTTPS` – set to `true` to always send the
  `Strict-Transport-Security` header, even if the request is not detected as
  secure (useful when SSL termination happens upstream). Defaults to `false`.
- `MAILGUN_ALLOWED_SENDERS` – optional comma-separated sender email allowlist (checked before domain checks).
- `MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS` – optional comma-separated attachment extension allowlist; defaults to `xls,xlsx`.
- `MAILGUN_WEBHOOK_MAX_AGE_SECONDS` – maximum accepted age for Mailgun timestamps (defaults to `900`).
- `MAILGUN_INBOUND_STORAGE_DIR` – optional absolute path for inbound attachment staging; defaults to `<UPLOAD_FOLDER>/mailgun_inbound`.
- `POS_IMPORT_POLL_PROVIDER` – mailbox polling backend when `POS_IMPORT_INGEST_MODE=poll`; supported values: `imap` (default) and `api`.
- `POS_IMPORT_POLL_INTERVAL_SECONDS` – poller frequency in seconds; defaults to `3600` (hourly).
- `POS_IMPORT_IMAP_HOST` / `POS_IMPORT_IMAP_PORT` / `POS_IMPORT_IMAP_USERNAME` / `POS_IMPORT_IMAP_PASSWORD` – required when `POS_IMPORT_POLL_PROVIDER=imap`.
- `POS_IMPORT_IMAP_MAILBOX` – IMAP mailbox folder to monitor for unseen messages (defaults to `INBOX`).
- `POS_IMPORT_IMAP_USE_SSL` – set to `false` to use plaintext IMAP instead of IMAPS (defaults to `true`).
- `POS_IMPORT_API_BASE_URL` / `POS_IMPORT_API_TOKEN` – required when `POS_IMPORT_POLL_PROVIDER=api`.
- `POS_IMPORT_API_MESSAGES_PATH` – API path used to fetch unseen messages (defaults to `/messages/unseen`).
- `POS_IMPORT_API_ACK_PATH_TEMPLATE` – API path template used to acknowledge processed messages (defaults to `/messages/{message_id}/ack`).

Mailgun should post inbound events to `POST /webhooks/mailgun/inbound`.

### POS Sales Ingestion Modes

- **Default (`POS_IMPORT_INGEST_MODE=webhook`)**: inbound email attachments are pushed by Mailgun to `POST /webhooks/mailgun/inbound`.
- **Fallback (`POS_IMPORT_INGEST_MODE=poll`)**: a background worker polls the configured mailbox provider every hour (or `POS_IMPORT_POLL_INTERVAL_SECONDS`), fetches unseen messages, and stages `.xls` / `.xlsx` attachments through the **same parser and staging pipeline** used by webhook ingestion.

<details>
<summary><strong>Advanced: Poll mode operational setup</strong></summary>

1. Set `POS_IMPORT_INGEST_MODE=poll`.
2. Configure provider settings:
   - IMAP: set `POS_IMPORT_POLL_PROVIDER=imap` and IMAP credentials/host variables.
   - API: set `POS_IMPORT_POLL_PROVIDER=api` and API URL/token variables.
3. Set `MAILGUN_ALLOWED_SENDERS` or `MAILGUN_ALLOWED_SENDER_DOMAINS`; poll mode rejects all senders until one of these allowlists is configured.
4. Keep `MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS` configured as needed; the same extension allowlist is applied in poll mode.
5. Ensure the process remains running continuously so the background poller thread can execute hourly checks.

</details>

<details>
<summary><strong>Advanced: Failure handling and idempotency</strong></summary>

- Each attachment is hashed and staged with idempotency on `(source_provider, message_id, attachment_sha256)`, so duplicate polling runs do not create duplicate imports.
- Parse failures produce a `failed` `PosSalesImport` record with a `failure_reason`, while successful parses remain `pending` for the standard mapping/approval workflow.
- Messages are acknowledged only after all supported attachments in that message are processed without staging errors; failed messages remain unseen/unacknowledged for retry on the next polling pass.

</details>

## Database Setup & Migrations

Use this sequence for **host/venv installs** (not Docker Compose):

1. **Run migrations**
   ```bash
   python -m flask --app run.py db upgrade
   ```
2. **Seed admin account + default settings**
   ```bash
   python seed_data.py
   ```

> **Note:** `setup.sh` and `setup.ps1` already run these steps. Only run them manually if you performed installation yourself.
>
> For Docker Compose flows, use the canonical Compose startup order in [Canonical Startup Order (Docker Compose)](#canonical-startup-order-docker-compose).

## Run the Application

After installing the dependencies and setting the environment variables, start the development server with:

```bash
python run.py
```

Set `PORT` in your environment to change the port (default `5000`).

The application connects to PostgreSQL using
`postgresql+psycopg://<user>:<password>@<host>:<port>/<database>`, assembled
from the `DATABASE_*` environment variables when you do not provide
`DATABASE_URL` or `SQLALCHEMY_DATABASE_URI`. The app also creates `uploads` and
`backups` directories automatically on startup.

## Backups & Restore (Postgres Runtime)

- **Backup artifact format remains SQLite `.db`.** The backup files in the
  `backups/` folder are still SQLite databases and remain the expected format
  for restore uploads.
- **Runtime database is PostgreSQL.** During restore, the app rebuilds the
  current runtime schema (via SQLAlchemy metadata) and then imports rows from
  the uploaded SQLite backup into matching tables/columns.
- **Migration prerequisite before restore:** ensure migrations are fully up to
  date (including `e3b7c9a1f4d2`, which expands `activity_log.activity` to
  `TEXT` so long audit messages from older backups restore cleanly) before
  attempting restore:
  ```bash
  python -m flask --app run.py db upgrade
  ```
  For Docker Compose:
  ```bash
  ./scripts/docker_migrate.sh
  ```

<details>
<summary><strong>Advanced: Restore troubleshooting</strong></summary>

If restore fails with FK/constraint errors (especially around
`invoice_product.invoice_id`), verify the target Postgres database has applied
revision `d2f7a1b9c8e0` and any later migrations, then retry the restore.
After migration `e3b7c9a1f4d2` is applied, long `activity_log.activity` entries
(>255 chars) are supported during restore.

</details>

For production deployments using Gunicorn, use the provided configuration to enable WebSocket support and prevent worker timeouts:

```bash
gunicorn -c gunicorn.conf.py run:app
```

For a repeatable Windows release smoke check, run:

```powershell
./scripts/release_verify.ps1
```

## Project Architecture

A high-level overview of the Flask application structure, shared services, and key data models is available in [docs/architecture.md](docs/architecture.md).

## Docker Setup

The project includes a `Dockerfile` and `docker-compose.yml` for containerized
runs on Linux and Windows. The image starts Gunicorn using `gunicorn.conf.py`.
Create a `.env` file with the variables listed above (including Redis-backed
`RATELIMIT_STORAGE_URI` for production rate limiting). The Compose stack now
starts `postgres`, `redis`, `metabase`, and `web` together. `HOST_DATABASE_PORT`
controls only the host-exposed Postgres port so it can avoid local port
conflicts without changing the app's internal `DATABASE_PORT=5432`, and
`METABASE_SITE_URL` controls where the dashboard's Metabase button redirects and
what Metabase itself uses as its public site URL for generated links.

For day-to-day local development, follow the canonical sequence in
[Canonical Startup Order (Docker Compose)](#canonical-startup-order-docker-compose).

If you want a one-command boot that builds images and starts services,
`docker compose up --build` remains supported; the web container entrypoint
runs migrations before Gunicorn starts.

When you are testing local code changes from this checkout, use
`docker compose up --build` so the `web` service rebuilds from the local
`Dockerfile` instead of continuing to run an older cached/pulled image.

## Command Reference

Use these as quick-reference commands depending on whether you are running on
your host (virtualenv) or with Docker Compose.

| Action | Command | Notes/context (host vs Docker) |
| --- | --- | --- |
| Start app | `python run.py` | Host/venv flow. |
| Start app (Docker) | `docker compose up -d web` | Docker Compose flow after migrations; `postgres` and `redis` must already be up or be started with `docker compose up -d`. |
| Run migrations | `python -m flask --app run.py db upgrade` | Host/venv flow. |
| Run migrations (Docker) | `./scripts/docker_migrate.sh` | Preferred Docker wrapper; equivalent to running migration inside the web service. |
| Seed data | `python seed_data.py` | Host/venv flow (first boot or reset). |
| Seed data (Docker) | `docker compose run --rm web python seed_data.py` | Docker Compose first boot only. |
| Run tests | `pytest` | Host/venv flow. |
| Run tests (Docker) | `docker compose run --rm web pytest` | Useful when tests should run inside container environment. |
| Run pre-commit | `pre-commit run --all-files` | Host/venv flow from repo root. |
| Run pre-commit (Docker) | `docker compose run --rm web pre-commit run --all-files` | Requires `pre-commit` available in the container image. |
| Docker reset workflow | `docker compose down -v && docker compose up --build` | Docker only; removes local Postgres volume and rebuilds containers. |

## Setup Paths

- **I want the fastest Docker Compose path** → Start with [Docker Setup](#docker-setup), then follow [Canonical Startup Order (Docker Compose)](#canonical-startup-order-docker-compose).
- **I want a host/venv install** → Follow [Installation (Detailed)](#installation-detailed) and then the host/venv canonical steps in [Database Setup & Migrations](#database-setup-migrations).
- **I need production/runtime notes** → Review [Run the Application](#run-the-application) and [Backups & Restore (Postgres Runtime)](#backups-restore-postgres-runtime).
- **I am troubleshooting startup or DB issues** → Jump to [Troubleshooting Database Connection Issues](#troubleshooting-database-connection-issues).

<details>
<summary><strong>Advanced: Migration command inventory and fallback validation</strong></summary>

Migration execution points (single reference list):

1. **Container startup**: `entrypoint.sh` runs `flask db upgrade` before Gunicorn starts.
2. **Host setup scripts**: `setup.sh` and `setup.ps1` run `python -m flask --app run.py db upgrade`.
3. **Manual Compose migration**: `./scripts/docker_migrate.sh` (preferred) or `docker compose run --rm web flask db upgrade`.

Compose reminder: keep `DATABASE_HOST=postgres` (service DNS), and use
`./scripts/docker_migrate.sh` for explicit runs after pulling new migrations.
If `.env` defines only `DATABASE_*` values (without `DATABASE_URL`), you can
validate fallback URL assembly with:

```bash
./scripts/check_docker_migrate_env_fallback.sh
```

For the full Compose startup sequence (including when to seed data), use
[Canonical Startup Order (Docker Compose)](#canonical-startup-order-docker-compose).

</details>

### Canonical Startup Order (Docker Compose)

Use this order for consistent local boots. For Compose startup, keep
`DATABASE_HOST=postgres` (service DNS name):

1. **Start services needed for DB access**
   ```bash
   docker compose up -d postgres redis metabase
   ```
2. **Run migrations against Postgres-backed `DATABASE_URL`**
   ```bash
   ./scripts/docker_migrate.sh
   ```
3. **(First boot only) seed admin account + defaults**
   ```bash
   docker compose run --rm web python seed_data.py
   ```
4. **Start and verify the app**
   ```bash
   docker compose up -d web
   docker compose ps
   curl -I http://localhost:${PORT:-5000}/
   ```

You can run phases 1, 2, and 4 with:

```bash
./scripts/docker_start_verify.sh
```

### Reset Workflow (Fresh Local Database)

To reset local state and reinitialize PostgreSQL from scratch:

```bash
docker compose down -v
docker compose up --build
```

`docker compose down -v` removes the `postgres_data` volume, so all database
data is deleted and recreated on the next startup.

### Troubleshooting Database Connection Issues

<details>
<summary><strong>Advanced: Troubleshooting database connection issues</strong></summary>

- **Wrong host from your machine vs. containers:** use `DATABASE_HOST=postgres`
  in `.env` when the app runs inside Docker Compose; use `localhost` only for
  tools running directly on your host.
- **Credential mismatch:** ensure `DATABASE_USER`, `DATABASE_PASSWORD`, and
  `DATABASE_NAME` in `.env` match the Postgres container values. If you changed
  them after initial startup, run `docker compose down -v` and start again to
  recreate the database with the new credentials.
- **Container startup timing:** if the app fails early with connection refused,
  restart after Postgres becomes healthy:
  ```bash
  docker compose ps
  docker compose logs postgres
  docker compose restart web
  ```
- **Host Postgres port already in use:** if Compose cannot bind `127.0.0.1:5432`,
  set `HOST_DATABASE_PORT` in `.env` to another free host port such as `55432`.
- **Explicit connection string override:** if `DATABASE_URL` is set, it takes
  precedence over individual `DATABASE_*` values. Ensure the URL points to the
  correct host/port/user/password/database.

</details>

The repository includes an `import_files` directory containing example CSV files
that can be used as templates for data imports.

The web interface will be available at `http://localhost:$PORT` (default
`5000`). Uploaded files, import templates, and backups are stored on the host
in `uploads`, `backups`, and `import_files`. PostgreSQL data is persisted in the
`postgres_data` Docker volume. These locations are created automatically when
the container starts.

## Testing

The project includes a suite of `pytest` tests. Execute them with:

```bash
pytest
```

The tests automatically set the necessary admin/mail defaults. For PostgreSQL,
pytest uses `TEST_DATABASE_URL` when you provide it; otherwise it derives a
host-side connection from `DATABASE_*` / `HOST_DATABASE_PORT` and runs each test
inside an isolated schema.

## Code Style

This project uses [pre-commit](https://pre-commit.com/) to run formatting and
linting via **Black**, **isort**, and **Flake8**.

Install the development dependencies and set up the hooks:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Run all checks against the entire codebase with:

```bash
pre-commit run --all-files
```

A GitHub Actions workflow (`.github/workflows/format.yml`) executes these checks
for every pull request.

## Features
- Manage items, products, and invoices.
- Create sales invoices with either saved products or invoice-only custom lines
  for ad hoc work such as repairs, services, and one-off fees.
- User authentication and admin features.
- Reporting and backups.

## Documentation

- [Routes reference](docs/routes.md) – overview of every Flask blueprint,
  their URL prefixes, dependencies, and shared patterns.

## Data Import

Administrators can quickly seed the database by uploading CSV files from the
**Control Panel → Data Imports** page. Example templates are available in the
`import_files` directory at the project root if you want to use them as a
starting point:

- `example_gl_codes.csv`
- `example_locations.csv` – includes a `products` column listing product names
  separated by semicolons. The import will fail if any product name cannot be
  matched exactly.
- `example_products.csv` – may include a `recipe` column listing item names with
  quantities and units separated by semicolons (e.g. `Buns:2:each;Patties:1:each`). The import will
  fail if any item name or unit cannot be matched exactly.
- `example_items.csv` – includes optional `cost`, `base_unit`, `gl_code` and `units`
  columns. The `units` column lists unit name and factor pairs separated by
  semicolons (e.g. `each:1;case:12`). The first unit becomes the receiving and
  transfer default. The `gl_code` column should reference an existing GL code.
- `example_customers.csv`
- `example_vendors.csv`
- `example_users.csv` – uses placeholder passwords; replace them before importing.

Visit **Control Panel → Data Imports** in the web interface, choose the
appropriate CSV file, and click the corresponding button to import each
dataset.

## Test Defaults

When running `pytest`, the fixtures in `tests/conftest.py` set up several default values so the application can start without manual configuration:

- `SECRET_KEY` defaults to `"testsecret"`
- `ADMIN_EMAIL` defaults to `"admin@example.com"`
- `ADMIN_PASS` defaults to `"adminpass"`
- `TEST_DATABASE_URL` is optional; when omitted, pytest derives a host-side URI from `DATABASE_*` / `HOST_DATABASE_PORT`
- Each test runs in an isolated Postgres schema that is created and dropped automatically
- Two GL codes (`4000` and `5000`) are populated if none exist

These defaults are provided for convenience during testing, but you can override any of the environment variables by exporting your own values before running the tests.



## Documentation Index

- [Architecture overview](docs/architecture.md) – Open this for a high-level map of application layers, key services, and module responsibilities.
- [Routes reference](docs/routes.md) – Open this when you need endpoint/blueprint coverage, URL prefixes, and request flow details.
- [Key data models](docs/key-data-models.md) – Open this to understand core SQLAlchemy models, relationships, and business-critical fields.
- [PostgreSQL migration guide](docs/postgres-migration.md) – Open this when migrating from SQLite-era setups or validating Postgres runtime expectations.
- [Pizza variance guide](docs/pizza_variance.md) – Open this for the pizza variance workflow, formulas, and troubleshooting guidance.
- [Changelog](CHANGELOG.md) – Open this to review version-by-version release notes and notable changes.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
