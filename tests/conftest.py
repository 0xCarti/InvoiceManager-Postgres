from __future__ import annotations

import os
import sys
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from flask_migrate import upgrade
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app import create_app, create_admin_user, db, limiter
from app.models import GLCode, Setting
from app.permissions import sync_permission_data
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    serialize_conversion_setting,
)
from tests.utils import save_filter_defaults as _save_filter_defaults_helper

# Ensure the app package is importable when tests change directories
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

# Force deterministic admin credentials before pytest imports test modules that
# snapshot ADMIN_EMAIL/ADMIN_PASS at import time.
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASS"] = "adminpass"


def _with_search_path(database_uri: str, schema_name: str) -> str:
    """Return ``database_uri`` with a Postgres ``search_path`` option applied."""

    parts = urlsplit(database_uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema_name}"
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _host_accessible_database_uri(database_uri: str) -> str:
    """Return a URI rewritten for host-side access when Compose uses service DNS."""

    parts = urlsplit(database_uri)
    host = parts.hostname or "localhost"
    port = parts.port

    if host in {"postgres", "localhost"}:
        host = os.getenv("TEST_DATABASE_HOST", "127.0.0.1")
        host_port = os.getenv("TEST_DATABASE_PORT") or os.getenv("HOST_DATABASE_PORT")
        if host_port:
            port = int(host_port)

    netloc = ""
    if parts.username:
        netloc += parts.username
        if parts.password:
            netloc += f":{parts.password}"
        netloc += "@"
    netloc += host
    if port:
        netloc += f":{port}"

    return urlunsplit(
        (
            parts.scheme,
            netloc,
            parts.path,
            parts.query,
            parts.fragment,
        )
    )


def _default_test_database_uri() -> str:
    """Resolve the Postgres URI pytest should use for schema-isolated tests."""

    explicit_test_uri = os.getenv("TEST_DATABASE_URL")
    if explicit_test_uri:
        return explicit_test_uri

    driver = os.getenv("TEST_DATABASE_DRIVER") or os.getenv(
        "DATABASE_DRIVER", "postgresql+psycopg"
    )
    user = os.getenv("TEST_DATABASE_USER") or os.getenv("DATABASE_USER")
    password = os.getenv("TEST_DATABASE_PASSWORD") or os.getenv("DATABASE_PASSWORD")
    database_name = os.getenv("TEST_DATABASE_NAME") or os.getenv("DATABASE_NAME")
    database_host = os.getenv("TEST_DATABASE_HOST")
    database_port = os.getenv("TEST_DATABASE_PORT")

    if not database_host:
        configured_host = os.getenv("DATABASE_HOST")
        if configured_host and configured_host != "postgres":
            database_host = configured_host
            database_port = database_port or os.getenv("DATABASE_PORT")
        else:
            database_host = "127.0.0.1"
            database_port = (
                database_port
                or os.getenv("HOST_DATABASE_PORT")
                or os.getenv("DATABASE_PORT")
            )

    if database_host == "localhost":
        database_host = "127.0.0.1"

    if user and password and database_name:
        return (
            f"{driver}://{user}:{password}"
            f"@{database_host}:{database_port or '5432'}/{database_name}"
        )

    fallback_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if fallback_uri:
        return _host_accessible_database_uri(fallback_uri)

    return "postgresql+psycopg://invoicemanager:invoicemanager@localhost:5432/invoicemanager_test"


@pytest.fixture
def app(tmp_path):
    os.environ.setdefault("SECRET_KEY", "testsecret")
    os.environ["ADMIN_EMAIL"] = "admin@example.com"
    os.environ["ADMIN_PASS"] = "adminpass"
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_PORT", "25")
    os.environ.setdefault("SMTP_USERNAME", "user")
    os.environ.setdefault("SMTP_PASSWORD", "pass")
    os.environ.setdefault("SMTP_SENDER", "test@example.com")

    test_database_uri = _default_test_database_uri()
    admin_database_uri = test_database_uri
    test_schema = f"pytest_{uuid.uuid4().hex}"
    test_database_uri = _with_search_path(test_database_uri, test_schema)
    os.environ["SQLALCHEMY_DATABASE_URI"] = test_database_uri
    os.environ["DATABASE_URL"] = test_database_uri
    os.environ["SKIP_DB_CREATE_ALL"] = "1"
    os.environ["SQLALCHEMY_USE_NULL_POOL"] = "1"
    os.environ.setdefault("SQLALCHEMY_POOL_SIZE", "1")
    os.environ.setdefault("SQLALCHEMY_MAX_OVERFLOW", "0")
    admin_engine = create_engine(admin_database_uri, poolclass=NullPool)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    app, _ = create_app(["--demo"])
    os.chdir(cwd)

    app.config.update(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
        }
    )
    limiter.enabled = False
    limiter_extension = app.extensions.get("limiter")
    if hasattr(limiter_extension, "enabled"):
        limiter_extension.enabled = False

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{test_schema}"'))
        try:
            upgrade()
        except Exception:
            db.session.rollback()
        db.create_all()
        sync_permission_data(db.session)
        create_admin_user()
        if Setting.query.filter_by(name="GST").count() == 0:
            db.session.add(Setting(name="GST", value=""))
        if Setting.query.filter_by(name="DEFAULT_TIMEZONE").count() == 0:
            db.session.add(Setting(name="DEFAULT_TIMEZONE", value="UTC"))
        if Setting.query.filter_by(name="BASE_UNIT_CONVERSIONS").count() == 0:
            db.session.add(
                Setting(
                    name="BASE_UNIT_CONVERSIONS",
                    value=serialize_conversion_setting(
                        DEFAULT_BASE_UNIT_CONVERSIONS
                    ),
                )
            )
        db.session.commit()

        yield app
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
        error_log_path = app.config.get("ERROR_LOG_PATH")
        if error_log_path:
            normalized_error_log_path = os.path.abspath(error_log_path)
            for handler in list(app.logger.handlers):
                if os.path.abspath(
                    getattr(handler, "baseFilename", "")
                ) != normalized_error_log_path:
                    continue
                app.logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        drop_schema_sql = text(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        last_error = None
        for attempt in range(5):
            try:
                with admin_engine.begin() as conn:
                    conn.execute(drop_schema_sql)
                last_error = None
                break
            except OperationalError as exc:
                last_error = exc
                if "deadlock detected" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if last_error is not None:
            raise last_error
        admin_engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def save_filter_defaults(client):
    """Return a helper that stores filter defaults via the preferences API."""

    def _save(scope: str, values: dict[str, list[str]], *, token_path: str = "/items"):
        return _save_filter_defaults_helper(
            client, scope, values, token_path=token_path
        )

    return _save


@pytest.fixture(autouse=True)
def gl_codes(app):
    with app.app_context():
        if GLCode.query.count() == 0:
            db.session.add_all([GLCode(code="4000"), GLCode(code="5000")])
            db.session.commit()
