# Postgres Migration Readiness Checklist

> **Merge gate:** This checklist **must be fully completed** before merging any Postgres migration change.

Use this document during validation runs and attach command output to the PR where practical.

## Checklist

> For Docker Compose migration/startup workflows, keep `DATABASE_HOST=postgres` because Compose service discovery uses service names (not `container_name`).

- [ ] Fresh `docker compose up` succeeds.
- [ ] `flask db upgrade` succeeds from an empty DB.
- [ ] App boots and basic CRUD paths work.
- [ ] Tests pass against Postgres config.

---

## 1) Fresh `docker compose up` succeeds

### Command

```bash
docker compose up --build
```

### Expected outcome

- Docker builds images without build errors.
- Database container becomes healthy/ready.
- Web/app container starts without crash loops.
- Logs show the app serving requests (for example, Flask/Gunicorn startup messages).

### Completion criteria

- [ ] Mark complete only after a full fresh boot reaches healthy/running state.

---

## 2) `flask db upgrade` succeeds from empty DB

### Command

```bash
docker compose run --rm web flask db upgrade
```

> If your service name is not `web`, replace it with the app service from `docker-compose.yml`.

### Expected outcome

- Migration command exits with status code `0`.
- Alembic reports migrations applied successfully (no traceback/errors).
- Postgres schema objects are created in the target database.

### Completion criteria

- [ ] Mark complete only after running against a newly initialized/empty database.

---

## 3) App boots and basic CRUD paths work

### Commands

```bash
docker compose up -d
curl -f http://localhost:5000/
```

Then validate one representative CRUD flow (create/read/update/delete) through either UI or HTTP endpoints.

### Expected outcome

- App responds successfully on the configured port.
- Create operation persists a new record.
- Read operation returns the created record.
- Update operation persists changes.
- Delete operation removes the record and subsequent read confirms removal.

### Completion criteria

- [ ] Mark complete only after end-to-end CRUD verification against Postgres-backed app data.

---

## 4) Tests pass against Postgres config

### Command

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/invoice_manager_test pytest
```

> Adjust credentials/host/db name to match your local Postgres test configuration.

### Expected outcome

- Test suite exits with status code `0`.
- No Postgres driver/connection failures.
- No migration/schema mismatch errors during test setup.

### Completion criteria

- [ ] Mark complete only after full required test scope passes under Postgres settings.

---

## Merge requirement

All checklist items above must be checked (`[x]`) before merge approval.

---

## Restore compatibility requirement (SQLite backup → Postgres runtime)

Restores continue to accept SQLite `.db` backup files as the source artifact.
The running application database is PostgreSQL; restore logic rebuilds the
runtime schema and then imports backup rows into matching Postgres tables.

Before any restore attempt, migrations **must** include revision
`d2f7a1b9c8e0` (`fix_invoice_product_invoice_fk`), which corrects the
`invoice_product.invoice_id` foreign key behavior expected during row import.

### Quick pre-restore check

```bash
docker compose run --rm web flask db current
docker compose run --rm web flask db upgrade
```

### Restore troubleshooting note

If a restore fails with foreign-key or constraint errors, treat it first as a
schema migration state issue: verify `d2f7a1b9c8e0` (and later revisions) are
applied on the target Postgres database, then retry restore.
