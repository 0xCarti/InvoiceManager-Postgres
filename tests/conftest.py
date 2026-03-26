from __future__ import annotations

import os
import sys
import uuid

import pytest

from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app, create_admin_user, db
from app.models import GLCode, Setting
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    serialize_conversion_setting,
)
from tests.utils import save_filter_defaults as _save_filter_defaults_helper

# Ensure the app package is importable when tests change directories
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)


@pytest.fixture
def app(tmp_path):
    os.environ.setdefault("SECRET_KEY", "testsecret")
    os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
    os.environ.setdefault("ADMIN_PASS", "adminpass")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_PORT", "25")
    os.environ.setdefault("SMTP_USERNAME", "user")
    os.environ.setdefault("SMTP_PASSWORD", "pass")
    os.environ.setdefault("SMTP_SENDER", "test@example.com")

    test_database_uri = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://invoicemanager:invoicemanager@localhost:5432/invoicemanager_test",
    )
    test_schema = f"pytest_{uuid.uuid4().hex}"
    os.environ["SQLALCHEMY_DATABASE_URI"] = test_database_uri
    os.environ["DATABASE_URL"] = test_database_uri

    cwd = os.getcwd()
    os.chdir(tmp_path)
    app, _ = create_app(["--demo"])
    os.chdir(cwd)

    app.config.update(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "connect_args": {"options": f"-csearch_path={test_schema}"}
            },
        }
    )

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
        with db.engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{test_schema}"'))
        try:
            upgrade()
        except Exception:
            db.session.rollback()
        db.create_all()
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
        with db.engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE'))


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
