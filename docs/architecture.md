# Project Architecture

This document provides a high-level overview of the InvoiceManager Flask
application. It is intended to help contributors understand how the
application is assembled and where to look when adding new features or fixing
bugs.

> PostgreSQL is the only supported runtime database path.

## Application Factory (`app/__init__.py`)

InvoiceManager is built around a Flask application factory defined in
[`app/__init__.py`](../app/__init__.py). The factory creates the Flask
application, configures core services, and registers blueprints for every
feature module.

Key responsibilities include:

* Loading environment variables and critical settings (secret key, PostgreSQL connection settings, upload
  paths, backup directories).
* Configuring global extensions: SQLAlchemy for persistence, Flask-Login for
  authentication, Flask-Limiter for rate limiting, Flask-Bootstrap for UI
  helpers, Flask-SocketIO for real-time updates, and CSRF protection for forms.
* Initialising the database (creating tables if migrations have not yet been
  applied) and ensuring the default admin account exists.
* Registering blueprints under `app/routes/…`, so each functional area (items,
  transfers, invoices, reports, administration, etc.) owns its URLs and view
  logic.
* Adding global template context helpers (navigation links, pagination sizes,
  GST value, CSP nonce) and HTTP hardening hooks (CSP, security headers,
  options blocking).

The factory returns both the configured Flask application and the `SocketIO`
instance so that entry points such as `run.py` can start the appropriate
server.

## Shared Services and Utilities

Several extension instances are created at module import time in
`app/__init__.py` and reused throughout the project:

* `db`: The SQLAlchemy instance backing models in `app/models.py` and used in
  blueprints, CLI tasks, and migrations.
* `limiter`: Configured with the `get_remote_address` key function. Blueprints
  can import it to decorate routes that require rate limiting.
* `socketio`: Created inside the factory and exposed for WebSocket-based
  interactions (for example, pushing real-time updates to connected clients).
* `login_manager`: Manages user sessions and integrates with Flask-Login via
  the `load_user` callback in `app/__init__.py`.

Additional functionality lives under `app/utils/`. These modules encapsulate
cross-cutting concerns such as pagination helpers, automatic database backups,
file import routines, and shared validation logic. They are imported where
needed by blueprints or the factory to keep route files focused on request
handling.

### Standard text matching policy for list/search UIs

List and search pages must use a **single shared text-match policy** implemented
in [`app/utils/text.py`](../app/utils/text.py):

* Supported match modes are `exact`, `startswith`, `contains`, and `not_contains`.
* `startswith`, `contains`, and `not_contains` are always case-insensitive and
  must be built via `build_text_match_predicate(...)` (which uses `ILIKE` /
  `NOT ILIKE` semantics).
* Unknown or missing match modes normalize to `contains` via
  `normalize_text_match_mode(...)`.

Do not hand-roll route-specific `.like(...)` or `.ilike(...)` conditions for
list/search name filters. Reusing the shared helper prevents accidental
case-sensitive regressions and keeps behavior consistent across pages.

Dashboard aggregation logic is intentionally grouped in
`app/services/dashboard_metrics.py`. Dashboard trend requests may provide an
`interval` query parameter (for example `/?interval=quarter`), with supported
keys `week`, `month`, `quarter`, `half_year`, and `year`. When no interval key
is supplied, aggregation defaults to `week`.

To keep visualizations consistent, bucket boundaries are derived from canonical
period starts (`_interval_start`) and interval stepping (`_add_interval`), and
bucket labels are produced from those same computed start/end boundaries inside
`weekly_transfer_purchase_activity` (`"%b %d – %b %d"` format). Any future
interval change should update boundary and label rules together so table/chart
representations stay aligned.

## Data Models

All persistent data structures are defined in [`app/models.py`](../app/models.py).
The module declares SQLAlchemy models for core business concepts including
users, inventory items, products, transfers, invoices, purchase orders, events,
and configuration settings. Relationships capture how these concepts relate;
for example, transfers and invoices link back to their creators, items connect
products to recipes, and events tie locations to terminal sales. A short
summary of the most important models appears in [Key Data Models](#key-data-models-reference).

## Templates and Static Assets

HTML templates live under `app/templates/` and extend shared layouts. They
consume context injected by the factory (navigation links, CSP nonce, GST value)
and render data retrieved by blueprint routes. JavaScript and CSS assets reside
in `app/static/`, alongside Bootstrap resources. Jinja filters like
`format_datetime` (registered in the factory) help templates display timezone
aware timestamps.

## Request Flow Overview

1. An entry point (e.g. `run.py`) calls the application factory to obtain the
   Flask app and `SocketIO` server.
2. When a request arrives, Flask routes it to the appropriate blueprint view
   function. Rate limiting or authentication decorators provided by the shared
   services may run before the view logic.
3. Views interact with SQLAlchemy models via the shared `db` session and may
   call into `app/utils/` helpers to perform domain-specific operations.
4. Responses render templates or return JSON. Template rendering leverages the
   shared context processors and filters registered in the factory. Real-time
   responses can emit `socketio` events when necessary.
   External systems can also feed machine-to-machine POS sales imports through
   two interchangeable ingestion modes:
   - webhook mode (`POS_IMPORT_INGEST_MODE=webhook`, default), where Mailgun
     posts inbound events to `POST /webhooks/mailgun/inbound`;
   - poll mode (`POS_IMPORT_INGEST_MODE=poll`), where an hourly background
     mailbox poller (IMAP or configured API provider) fetches unseen messages.
   Both modes run the same attachment parser + staging pipeline and preserve
   identical idempotency behavior.
5. Background helpers such as the automatic backup thread (started during app
   creation) and optional POS mailbox poller run independently, using the app
   context as needed.

## Key Data Models Reference

Refer to [Key Data Models](key-data-models.md) to become familiar with the domain entities before exploring routes or templates.
