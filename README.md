# Invoice Manager
[![codecov](https://codecov.io/github/0xCarti/InvoiceManager/branch/main/graph/badge.svg?token=GDFIVY6JX6)](https://codecov.io/github/0xCarti/InvoiceManager)
[![Build status](https://github.com/0xCarti/InvoiceManager/actions/workflows/build-main.yml/badge.svg?branch=main)](https://github.com/0xCarti/InvoiceManager/actions/workflows/build-main.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Flask-based application for managing invoices, products and vendors. The project comes with a comprehensive test suite using `pytest`.

PostgreSQL is the only supported runtime database backend.

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

## Required Environment Variables

The application requires several variables to be present in your environment:

- `SECRET_KEY` – Flask secret key used for sessions.
- `ADMIN_EMAIL` – email address for the initial administrator account.
- `ADMIN_PASS` – password for the administrator account.
- `PORT` – port the web server listens on (optional, defaults to 5000).
- `DATABASE_DRIVER` – SQLAlchemy driver name (defaults to `postgresql+psycopg`).
- `DATABASE_HOST` – PostgreSQL host (defaults to `postgres`).
- `DATABASE_PORT` – PostgreSQL port (defaults to `5432`).
- `DATABASE_USER` – PostgreSQL username (defaults to `invoicemanager`).
- `DATABASE_PASSWORD` – PostgreSQL password (defaults to `invoicemanager`).
- `DATABASE_NAME` – PostgreSQL database name (defaults to `invoicemanager`).
- `DATABASE_URL` / `SQLALCHEMY_DATABASE_URI` – optional full SQLAlchemy URI
  override (takes precedence over individual `DATABASE_*` values).
- `SQLALCHEMY_POOL_PRE_PING` – enables stale-connection checks before checkout
  (defaults to `true`).
- `SQLALCHEMY_POOL_RECYCLE` – recycles pooled connections after this many
  seconds (defaults to `1800`).
- `SQLALCHEMY_POOL_TIMEOUT` – seconds to wait for a pooled connection before
  timing out (defaults to `30`).
- `SQLALCHEMY_POOL_SIZE` – steady-state number of pooled connections per
  process (defaults to `5`).
- `SQLALCHEMY_MAX_OVERFLOW` – extra burst connections allowed above
  `SQLALCHEMY_POOL_SIZE` (defaults to `10`).
- `SQLALCHEMY_POOL_USE_LIFO` – use LIFO checkout behavior to let older idle
  connections expire naturally in containerized environments (defaults to
  `true`).
- `SMTP_HOST` – hostname of your SMTP server.
- `SMTP_PORT` – port for the SMTP server (defaults to 25).
- `SMTP_USERNAME` – username for SMTP authentication.
- `SMTP_PASSWORD` – password for SMTP authentication.
- `SMTP_SENDER` – email address used as the sender.
- `SMTP_USE_TLS` – set to `true` to enable TLS.
- `RATELIMIT_STORAGE_URI` – URI for the rate limiting backend. Use a
  persistent store such as Redis in production (e.g., `redis://redis:6379/0`).
- `MAILGUN_WEBHOOK_SIGNING_KEY` – Mailgun inbound signing key used to verify webhook authenticity.
- `MAILGUN_ALLOWED_SENDER_DOMAINS` – comma-separated sender domains allowed to submit imports (for example `example.com`).
- `POS_IMPORT_INGEST_MODE` – POS import ingestion strategy. Use `webhook` (default) to accept Mailgun inbound webhooks, or `poll` to ingest from a mailbox provider on a schedule.

A persistent backing store is required for rate limiting in production. Set
`RATELIMIT_STORAGE_URI` to a supported service so that limits are shared
across workers.

These SMTP variables enable password reset emails. Configure them in your `.env` file if you want users to reset forgotten passwords.

The GST number can now be set from the application control panel after installation.

These can be placed in a `.env` file or exported in your shell before starting the app.

### Optional Environment Variables

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

#### Poll mode operational setup

1. Set `POS_IMPORT_INGEST_MODE=poll`.
2. Configure provider settings:
   - IMAP: set `POS_IMPORT_POLL_PROVIDER=imap` and IMAP credentials/host variables.
   - API: set `POS_IMPORT_POLL_PROVIDER=api` and API URL/token variables.
3. Keep `MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS` configured as needed; the same extension allowlist is applied in poll mode.
4. Ensure the process remains running continuously so the background poller thread can execute hourly checks.

#### Failure handling and idempotency

- Each attachment is hashed and staged with idempotency on `(source_provider, message_id, attachment_sha256)`, so duplicate polling runs do not create duplicate imports.
- Parse failures produce a `failed` `PosSalesImport` record with a `failure_reason`, while successful parses remain `pending` for the standard mapping/approval workflow.
- Messages are acknowledged only after all supported attachments in that message are processed without staging errors; failed messages remain unseen/unacknowledged for retry on the next polling pass.

## Database Setup

Run the database migrations to create the tables (host install):

```bash
python -m flask --app run.py db upgrade
```

After the migration, seed the initial administrator account and default
settings (GST number and timezone) using the provided script:

```bash
python seed_data.py
```

When you run the app in Docker Compose, use the container-aware helper instead
of running Flask commands directly on your host:

```bash
./scripts/docker_migrate.sh
```

> **Note:** Both setup scripts execute these commands automatically after installing the dependencies. Run them manually only if you performed the installation steps yourself.

## Running the Application

After installing the dependencies and setting the environment variables, start the development server with:

```bash
python run.py
```

Set `PORT` in your environment to change the port (default `5000`).

By default, the application connects to PostgreSQL using
`postgresql+psycopg://<user>:<password>@<host>:<port>/<database>`, assembled
from the `DATABASE_*` environment variables. You can override this with
`DATABASE_URL` or `SQLALCHEMY_DATABASE_URI`. The app also creates `uploads` and
`backups` directories automatically on startup.

For production deployments using Gunicorn, use the provided configuration to enable WebSocket support and prevent worker timeouts:

```bash
gunicorn -c gunicorn.conf.py run:app
```

## Project Architecture

A high-level overview of the Flask application structure, shared services, and key data models is available in [docs/architecture.md](docs/architecture.md).

## Docker Setup

The project includes a `Dockerfile` and a `docker-compose.yml` to make running
the application in a container straightforward on Linux and Windows. The image
starts Gunicorn using the included `gunicorn.conf.py`, so no additional commands
are required. Create a `.env` file containing the environment variables
described above. A persistent backing service such as Redis is required for
rate limiting in production; set `RATELIMIT_STORAGE_URI` to its connection
string. You can also specify the port the app will use by adding a `PORT`
variable to `.env` (or by exporting it in your shell) before starting the
service. Database migrations run automatically when the container starts via
`entrypoint.sh`, rather than during the image build:

> **Deployment tag note:** operators pulling only `docker-compose.yml` + `.env` should use `ghcr.io/0xcarti/invoice-manager-postgres:2026.03.26-password255` (not `:latest`) to ensure this password-column migration is present.

```bash
docker compose up --build
```

### Local startup (single command)

For day-to-day local development, once your `.env` exists, use one command:

```bash
docker compose up --build
```

This starts both `postgres` and `web`, waits for PostgreSQL health checks, runs
database migrations from the web container entrypoint, and serves the app on
`http://localhost:${PORT:-5000}`.


### Migration command inventory

The team currently runs migrations in three places:

1. **Container startup**: `entrypoint.sh` runs `flask db upgrade` automatically
   before Gunicorn starts.
2. **Host setup scripts**: `setup.sh` and `setup.ps1` run
   `python -m flask --app run.py db upgrade` for non-container setups.
3. **Manual container workflow**: `docker compose run --rm web flask db upgrade`
   (or `./scripts/docker_migrate.sh`) for explicit migration runs.

For Docker Compose workflows, scripts should always run with a
Postgres-backed `DATABASE_URL` pointing to the Compose `postgres` service
name (not `container_name`). Keep `DATABASE_HOST=postgres` for these flows.
`./scripts/docker_migrate.sh` enforces that automatically.
When `.env` only provides `DATABASE_*` values (without `DATABASE_URL`), verify
the fallback URL assembly with:

```bash
./scripts/check_docker_migrate_env_fallback.sh
```

### First-time database initialization and migrations

On first boot, migrations run automatically. If you want to run the steps
manually (for example while debugging), use container-aware commands. Keep
`DATABASE_HOST=postgres` so the web service can resolve the Postgres service
via Docker Compose DNS:

```bash
docker compose up -d postgres
./scripts/docker_migrate.sh
docker compose run --rm web python seed_data.py
docker compose up -d web
```

Use `./scripts/docker_migrate.sh` after pulling new migrations so the command
runs with a Compose-compatible `DATABASE_URL`.

### Canonical local startup order (Docker Compose)

Use this order for consistent local boots. For Compose startup, keep
`DATABASE_HOST=postgres` (service DNS name):

1. **Start services needed for DB access**
   ```bash
   docker compose up -d postgres
   ```
2. **Run migrations against Postgres-backed `DATABASE_URL`**
   ```bash
   ./scripts/docker_migrate.sh
   ```
3. **Start and verify the app**
   ```bash
   docker compose up -d web
   docker compose ps
   curl -I http://localhost:${PORT:-5000}/
   ```

You can run all three phases with:

```bash
./scripts/docker_start_verify.sh
```

### Reset workflow (fresh local database)

To reset local state and reinitialize PostgreSQL from scratch:

```bash
docker compose down -v
docker compose up --build
```

`docker compose down -v` removes the `postgres_data` volume, so all database
data is deleted and recreated on the next startup.

### Troubleshooting database connection issues

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
- **Explicit connection string override:** if `DATABASE_URL` is set, it takes
  precedence over individual `DATABASE_*` values. Ensure the URL points to the
  correct host/port/user/password/database.

The repository includes an `import_files` directory containing example CSV files
that can be used as templates for data imports.

The web interface will be available at `http://localhost:$PORT` (default
`5000`). Uploaded files, import templates, and backups are stored on the host
in `uploads`, `backups`, and `import_files`. PostgreSQL data is persisted in the
`postgres_data` Docker volume. These locations are created automatically when
the container starts.

## Running Tests

The project includes a suite of `pytest` tests. Execute them with:

```bash
pytest
```

The tests automatically set the necessary environment variables, so no additional setup is required.

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
- `example_users.csv`

Visit **Control Panel → Data Imports** in the web interface, choose the
appropriate CSV file, and click the corresponding button to import each
dataset.

## Test Defaults

When running `pytest`, the fixtures in `tests/conftest.py` set up several default values so the application can start without manual configuration:

- `SECRET_KEY` defaults to `"testsecret"`
- `ADMIN_EMAIL` defaults to `"admin@example.com"`
- `ADMIN_PASS` defaults to `"adminpass"`
- `TEST_DATABASE_URL` defaults to `postgresql+psycopg://invoicemanager:invoicemanager@localhost:5432/invoicemanager_test`
- Each test runs in an isolated Postgres schema that is created and dropped automatically
- Two GL codes (`4000` and `5000`) are populated if none exist

These defaults are provided for convenience during testing, but you can override any of the environment variables by exporting your own values before running the tests.


## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
