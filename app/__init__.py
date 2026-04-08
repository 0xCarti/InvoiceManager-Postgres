import os
import re
import secrets
import sys
import traceback
import logging
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, url_for
from flask_bootstrap import Bootstrap
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user, logout_user
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import PendingRollbackError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.routing import BuildError
from werkzeug.security import generate_password_hash

from app.permissions import (
    get_default_landing_endpoint,
    sync_permission_data,
    user_can_access_endpoint,
)

load_dotenv()
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
storage_uri = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
limiter = Limiter(key_func=get_remote_address, storage_uri=storage_uri)
socketio = None
DEFAULT_MAX_UPLOAD_FILE_SIZE_BYTES = 10 * 1024 * 1024


def _get_bool_env(var_name: str, default: bool = False) -> bool:
    """Return a boolean environment variable value."""

    value = os.getenv(var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_env(var_name: str, default: int) -> int:
    """Return an integer environment variable value."""

    value = os.getenv(var_name)
    if value is None:
        return default
    return int(value)


def _should_auto_create_schema(args: list[str] | tuple[str, ...] | None = None) -> bool:
    """Decide whether the app should call ``db.create_all()`` on startup.

    The production and migration paths should rely on Alembic, not implicit
    schema creation. Keep the convenience bootstrap only for direct local app
    execution such as ``python run.py``.
    """

    args = list(args or [])
    if _get_bool_env("SKIP_DB_CREATE_ALL", default=False) or _get_bool_env(
        "FLASK_SKIP_CREATE_ALL", default=False
    ):
        return False

    # Flask CLI imports the app for commands like ``flask db upgrade`` and
    # ``flask routes``. Those commands must not create tables implicitly.
    if _get_bool_env("FLASK_RUN_FROM_CLI", default=False):
        return False

    executable = os.path.basename(args[0]).lower() if args else ""
    if executable in {"flask", "gunicorn"}:
        return False

    # Keep the historical convenience for direct app startup only.
    return True


def _redact_error_details(details: str) -> str:
    """Redact sensitive values from exception details shown in UI."""
    if not details:
        return ""

    redacted = details

    # Common key/value pairs: password=..., api_key: ..., token=...
    redacted = re.sub(
        (
            r"(?im)\b(password|passwd|pwd|secret|api[_-]?key|token|"
            r"session(?:id)?|cookie)\b\s*[:=]\s*([^\s,;\"']+)"
        ),
        r"\1=<redacted>",
        redacted,
    )
    # Authorization headers and bearer tokens.
    redacted = re.sub(
        r"(?im)\bauthorization\b\s*:\s*bearer\s+[^\s]+",
        "Authorization: Bearer <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bbearer\s+[a-z0-9\-._~+/]+=*",
        "Bearer <redacted>",
        redacted,
    )
    # URL credentials in connection strings (scheme://user:pass@host).
    redacted = re.sub(
        r"([a-zA-Z][a-zA-Z0-9+\-.]*://)([^/\s:@]+):([^@\s/]+)@",
        r"\1<redacted>:<redacted>@",
        redacted,
    )
    # Cookie and Set-Cookie header values.
    redacted = re.sub(
        r"(?im)\b(set-cookie|cookie)\b\s*:\s*.+",
        r"\1: <redacted>",
        redacted,
    )

    return redacted


def _truncate_error_details(details: str, max_length: int, error_token: str) -> str:
    """Truncate oversized error details and append support guidance."""
    if max_length <= 0 or len(details) <= max_length:
        return details
    truncated = details[:max_length].rstrip()
    return (
        f"{truncated}\n\n...[truncated]...\n"
        f"Trace output exceeded {max_length} characters. "
        f"See token {error_token} for full logs."
    )


def _build_user_error_details(
    traceback_text: str,
    *,
    show_detailed_trace: bool,
    max_length: int,
    error_token: str,
) -> str:
    """Return redacted details appropriate for end-user error pages."""
    redacted_details = _redact_error_details(traceback_text)
    if show_detailed_trace:
        return _truncate_error_details(redacted_details, max_length, error_token)

    summary_line = "An internal error occurred."
    for line in reversed(redacted_details.splitlines()):
        if line.strip():
            summary_line = line.strip()
            break
    return (
        f"{summary_line}\n\n"
        "Detailed traceback is hidden for safety. "
        f"Share token {error_token} with support for full logs."
    )


def _configure_error_file_logging(app: Flask) -> None:
    """Configure rotating file logging for unhandled error incidents."""
    logs_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    error_log_path = os.path.join(logs_dir, "errors.log")
    app.config["ERROR_LOG_PATH"] = error_log_path

    for handler in app.logger.handlers:
        if isinstance(handler, RotatingFileHandler) and os.path.abspath(
            getattr(handler, "baseFilename", "")
        ) == os.path.abspath(error_log_path):
            return

    rotating_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=int(os.getenv("ERROR_LOG_MAX_BYTES", 5 * 1024 * 1024)),
        backupCount=int(os.getenv("ERROR_LOG_BACKUP_COUNT", 5)),
        encoding="utf-8",
    )

    class _ErrorContextDefaultsFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            defaults = {
                "error_token": "-",
                "request_path": "-",
                "request_method": "-",
                "user_identity": "-",
                "remote_addr": "-",
                "user_agent": "-",
                "traceback_text": "-",
            }
            for key, value in defaults.items():
                if not hasattr(record, key):
                    setattr(record, key, value)
            return True

    rotating_handler.addFilter(_ErrorContextDefaultsFilter())
    rotating_handler.setLevel(logging.ERROR)
    rotating_handler.setFormatter(
        logging.Formatter(
            (
                "%(asctime)s %(levelname)s token=%(error_token)s "
                "path=%(request_path)s method=%(request_method)s "
                "user=%(user_identity)s remote_addr=%(remote_addr)s "
                "user_agent=%(user_agent)s traceback=%(traceback_text)s"
            )
        )
    )
    app.logger.addHandler(rotating_handler)
    app.logger.setLevel(logging.INFO)


DEFAULT_CSP_TEMPLATE = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' https://cdn.jsdelivr.net 'nonce-{nonce}'; "
    "font-src 'self' data:; "
    "connect-src 'self' wss: https://cdn.jsdelivr.net; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "base-uri 'self'"
)
GST = ""
RETAIL_POP_PRICE = "4.25"
DEFAULT_TIMEZONE = "UTC"
BASE_UNIT_CONVERSIONS = {}
NAV_LINKS = {
    "transfer.view_transfers": "Transfers",
    "item.view_items": "Items",
    "locations.view_locations": "Locations",
    "menu.view_menus": "Menus",
    "product.view_products": "Products",
    "spoilage.view_spoilage": "Spoilage",
    "glcode.view_gl_codes": "GL Codes",
    "purchase.view_purchase_orders": "Purchase Orders",
    "purchase.view_purchase_invoices": "Purchase Invoices",
    "customer.view_customers": "Customers",
    "vendor.view_vendors": "Vendors",
    "invoice.view_invoices": "Invoices",
    "event.view_events": "Events",
    "admin.users": "Control Panel",
    "admin.backups": "Backups",
    "admin.settings": "Settings",
    "admin.terminal_sales_mappings": "Terminal Sales Mappings",
    "admin.sales_imports": "Sales Import Review",
    "admin.import_page": "Data Imports",
    "admin.activity_logs": "Activity Logs",
    "admin.system_info": "System Info",
    "admin.vendor_item_aliases": "Vendor Item Aliases",
    "admin.permission_groups": "Permission Groups",
    "admin.permission_catalog": "Permissions",
}

NAV_GROUPS = (
    (
        "Sales",
        (
            ("invoice.view_invoices", "Invoices"),
            ("customer.view_customers", "Customers"),
            ("event.view_events", "Events"),
        ),
        False,
    ),
    (
        "Purchasing",
        (
            ("purchase.view_purchase_orders", "Purchase Orders"),
            ("purchase.view_purchase_invoices", "Purchase Invoices"),
            ("vendor.view_vendors", "Vendors"),
        ),
        False,
    ),
    (
        "Catalog",
        (
            ("item.view_items", "Items"),
            ("product.view_products", "Products"),
            ("menu.view_menus", "Menus"),
            ("locations.view_locations", "Locations"),
        ),
        False,
    ),
    (
        "Finance",
        (
            ("glcode.view_gl_codes", "GL Codes"),
            ("transfer.view_transfers", "Transfers"),
            ("spoilage.view_spoilage", "Spoilage"),
        ),
        False,
    ),
    (
        "Reports",
        (
            ("report.customer_invoice_report", "Customer Invoice Report"),
            ("report.received_invoice_report", "Received Invoice Report"),
            ("report.purchase_inventory_summary", "Purchase Inventory Summary"),
            ("report.inventory_variance_report", "Inventory Variance Report"),
            ("report.product_sales_report", "Revenue Report"),
            ("report.product_stock_usage_report", "Stock Usage Report"),
            ("report.department_sales_forecast", "Department Sales Forecast"),
            ("report.product_recipe_report", "Recipe Report"),
            ("report.product_location_sales_report", "Product Location Sales Report"),
            ("report.event_terminal_sales_report", "Event Terminal Sales Report"),
            ("report.purchase_cost_forecast", "Forecasted Stock Item Sales"),
            ("report.customer_invoice_report", "Vendor Invoices Report"),
        ),
        False,
    ),
    (
        "System/Admin",
        (
            ("admin.users", "Control Panel"),
            ("admin.settings", "Settings"),
            ("admin.backups", "Backups"),
            ("admin.import_page", "Data Imports"),
            ("admin.sales_imports", "Sales Import Review"),
            ("admin.activity_logs", "Activity Logs"),
            ("admin.terminal_sales_mappings", "Terminal Sales Mappings"),
            ("admin.vendor_item_aliases", "Vendor Item Aliases"),
            ("admin.permission_groups", "Permission Groups"),
            ("admin.permission_catalog", "Permissions"),
        ),
        True,
    ),
)

# Endpoints required for baseline navigation/rendering support.
MANDATORY_NAV_ENDPOINTS = [
    "transfer.view_transfers",
    "item.view_items",
    "locations.view_locations",
    "menu.view_menus",
    "event.view_events",
]


@login_manager.user_loader
def load_user(user_id):
    """Retrieve a user by ID for Flask-Login."""
    from app.models import User

    user = db.session.get(User, int(user_id))
    if user is None or not getattr(user, "active", False):
        return None
    return user


def create_admin_user():
    """Ensure an admin user exists for the application."""
    from app.models import User

    # Check if any admin exists
    admin_exists = User.query.filter_by(is_admin=True).first()
    if not admin_exists:

        # Create an admin user
        admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
        raw_password = os.getenv("ADMIN_PASS")
        if raw_password is None:
            raise RuntimeError("ADMIN_PASS environment variable not set")
        admin_password = generate_password_hash(raw_password)
        admin_user = User(
            email=admin_email,
            password=admin_password,
            is_admin=True,
            active=True,
        )

        db.session.add(admin_user)
        db.session.commit()
        print("Admin user created.")


def create_app(args=None):
    """Application factory used by Flask."""
    global socketio, GST, RETAIL_POP_PRICE, DEFAULT_TIMEZONE, BASE_UNIT_CONVERSIONS
    if args is None:
        args = sys.argv[1:]
    else:
        args = list(args)
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    default_secure_cookies = "--demo" not in args
    session_cookie_secure = _get_bool_env(
        "SESSION_COOKIE_SECURE", default=default_secure_cookies
    )
    enforce_https = _get_bool_env("ENFORCE_HTTPS", default=False)
    app.config["ENFORCE_HTTPS"] = enforce_https
    support_mode = _get_bool_env("SUPPORT_MODE", default=False)
    app.config["SUPPORT_MODE"] = support_mode
    app.config["SHOW_ERROR_DETAILS_TO_USERS"] = _get_bool_env(
        "SHOW_ERROR_DETAILS_TO_USERS",
        default=support_mode,
    )
    app.config["ERROR_DETAILS_MAX_LENGTH"] = int(
        os.getenv("ERROR_DETAILS_MAX_LENGTH", "8000")
    )
    app.config.update(
        SESSION_COOKIE_SECURE=session_cookie_secure,
        REMEMBER_COOKIE_SECURE=session_cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        REMEMBER_COOKIE_DURATION=timedelta(days=7),
    )
    app.config["START_TIME"] = datetime.utcnow()
    # Use absolute paths so that changing the working directory after app
    # creation does not break file references. This occurs in the test suite
    # which creates the app in a temporary directory and then changes back to
    # the original working directory.  Building the paths here ensures they
    # always point to the intended location.

    base_dir = os.getcwd()
    repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    explicit_database_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv(
        "DATABASE_URL"
    )
    if explicit_database_uri:
        app.config["SQLALCHEMY_DATABASE_URI"] = explicit_database_uri
    else:
        db_driver = os.getenv("DATABASE_DRIVER", "postgresql+psycopg")
        db_host = os.getenv("DATABASE_HOST", "postgres")
        db_port = os.getenv("DATABASE_PORT", "5432")
        db_user = os.getenv("DATABASE_USER")
        db_password = os.getenv("DATABASE_PASSWORD")
        db_name = os.getenv("DATABASE_NAME")
        missing_db_settings = [
            name
            for name, value in {
                "DATABASE_USER": db_user,
                "DATABASE_PASSWORD": db_password,
                "DATABASE_NAME": db_name,
            }.items()
            if not value
        ]
        if missing_db_settings:
            missing_display = ", ".join(missing_db_settings)
            raise RuntimeError(
                "Database configuration is incomplete. Set SQLALCHEMY_DATABASE_URI "
                "or DATABASE_URL, or define DATABASE_USER, DATABASE_PASSWORD, and "
                f"DATABASE_NAME. Missing: {missing_display}."
            )
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"{db_driver}://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if _get_bool_env("SQLALCHEMY_USE_NULL_POOL", default=False):
        from sqlalchemy.pool import NullPool

        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "poolclass": NullPool,
        }
    else:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": _get_bool_env("SQLALCHEMY_POOL_PRE_PING", default=True),
            "pool_recycle": _get_int_env("SQLALCHEMY_POOL_RECYCLE", 1800),
            "pool_timeout": _get_int_env("SQLALCHEMY_POOL_TIMEOUT", 30),
            "pool_size": _get_int_env("SQLALCHEMY_POOL_SIZE", 5),
            "max_overflow": _get_int_env("SQLALCHEMY_MAX_OVERFLOW", 10),
            "pool_use_lifo": _get_bool_env("SQLALCHEMY_POOL_USE_LIFO", default=True),
        }
    app.config["UPLOAD_FOLDER"] = os.path.join(base_dir, "uploads")
    app.config["BACKUP_FOLDER"] = os.path.join(base_dir, "backups")
    app.config["IMPORT_FILES_FOLDER"] = os.path.join(repo_dir, "import_files")
    max_upload_file_size = _get_int_env(
        "MAX_UPLOAD_FILE_SIZE_BYTES", DEFAULT_MAX_UPLOAD_FILE_SIZE_BYTES
    )
    app.config["MAX_UPLOAD_FILE_SIZE_BYTES"] = max_upload_file_size
    app.config["MAX_CONTENT_LENGTH"] = max_upload_file_size
    app.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = os.getenv(
        "MAILGUN_WEBHOOK_SIGNING_KEY", ""
    )
    app.config["MAILGUN_WEBHOOK_MAX_AGE_SECONDS"] = int(
        os.getenv("MAILGUN_WEBHOOK_MAX_AGE_SECONDS", "900")
    )
    app.config["MAILGUN_ALLOWED_SENDERS"] = os.getenv("MAILGUN_ALLOWED_SENDERS", "")
    app.config["MAILGUN_ALLOWED_SENDER_DOMAINS"] = os.getenv(
        "MAILGUN_ALLOWED_SENDER_DOMAINS", ""
    )
    app.config["MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS"] = os.getenv(
        "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS", "xls,xlsx"
    )
    app.config["MAILGUN_INBOUND_STORAGE_DIR"] = os.getenv(
        "MAILGUN_INBOUND_STORAGE_DIR", ""
    )
    app.config["POS_IMPORT_MAX_ATTACHMENT_BYTES"] = _get_int_env(
        "POS_IMPORT_MAX_ATTACHMENT_BYTES", max_upload_file_size
    )
    app.config["POS_IMPORT_INGEST_MODE"] = os.getenv(
        "POS_IMPORT_INGEST_MODE", "webhook"
    )
    app.config["POS_IMPORT_POLL_PROVIDER"] = os.getenv(
        "POS_IMPORT_POLL_PROVIDER", "imap"
    )
    app.config["POS_IMPORT_POLL_INTERVAL_SECONDS"] = int(
        os.getenv("POS_IMPORT_POLL_INTERVAL_SECONDS", "3600")
    )
    app.config["POS_IMPORT_IMAP_HOST"] = os.getenv("POS_IMPORT_IMAP_HOST", "")
    app.config["POS_IMPORT_IMAP_PORT"] = int(os.getenv("POS_IMPORT_IMAP_PORT", "993"))
    app.config["POS_IMPORT_IMAP_USERNAME"] = os.getenv("POS_IMPORT_IMAP_USERNAME", "")
    app.config["POS_IMPORT_IMAP_PASSWORD"] = os.getenv("POS_IMPORT_IMAP_PASSWORD", "")
    app.config["POS_IMPORT_IMAP_MAILBOX"] = os.getenv(
        "POS_IMPORT_IMAP_MAILBOX", "INBOX"
    )
    app.config["POS_IMPORT_IMAP_USE_SSL"] = _get_bool_env(
        "POS_IMPORT_IMAP_USE_SSL", default=True
    )
    app.config["POS_IMPORT_API_BASE_URL"] = os.getenv("POS_IMPORT_API_BASE_URL", "")
    app.config["POS_IMPORT_API_TOKEN"] = os.getenv("POS_IMPORT_API_TOKEN", "")
    app.config["POS_IMPORT_API_MESSAGES_PATH"] = os.getenv(
        "POS_IMPORT_API_MESSAGES_PATH", "/messages/unseen"
    )
    app.config["POS_IMPORT_API_ACK_PATH_TEMPLATE"] = os.getenv(
        "POS_IMPORT_API_ACK_PATH_TEMPLATE", "/messages/{message_id}/ack"
    )
    app.config.setdefault(
        "RESTORE_REQUIRED_TABLES",
        ["setting", "user", "invoice", "transfer"],
    )
    app.config.setdefault("RESTORE_REQUIRED_FEATURE_FLAGS", [])
    app.config.setdefault("RESTORE_MODE_DEFAULT", "strict")
    app.config.setdefault("RESTORE_PREFLIGHT_STRICT_FK_VALIDATION", False)
    app.config.setdefault(
        "RESTORE_REPAIR_ORPHANS",
        _get_bool_env("RESTORE_REPAIR_ORPHANS", default=True),
    )
    app.config.setdefault(
        "RESTORE_ENDPOINT_EXPECTATIONS",
        [
            {
                "module": "core_navigation",
                "enabled": True,
                "endpoints": MANDATORY_NAV_ENDPOINTS,
            },
            {
                "module": "admin_backups",
                "enabled": True,
                "endpoints": ["admin.backups"],
            },
        ],
    )

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["BACKUP_FOLDER"], exist_ok=True)
    os.makedirs(app.config["IMPORT_FILES_FOLDER"], exist_ok=True)
    _configure_error_file_logging(app)

    if "--demo" in args:
        app.config["DEMO"] = True
    else:
        app.config["DEMO"] = False

    db.init_app(app)
    from flask_migrate import Migrate

    Migrate(app, db)
    login_manager.init_app(app)
    if app.config.get("TESTING"):
        app.config["RATELIMIT_ENABLED"] = False
    limiter.init_app(app)
    Bootstrap(app)
    socketio = SocketIO(app)

    from flask_login import current_user

    def format_datetime(value, fmt="%Y-%m-%d %H:%M:%S"):
        if value is None:
            return ""
        tz_name = getattr(current_user, "timezone", None)
        if not tz_name:
            tz_name = DEFAULT_TIMEZONE or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=tz)
            value = value.astimezone(tz)
        if sys.platform.startswith("win"):
            fmt = fmt.replace("%-", "%#")
        return value.strftime(fmt)

    app.jinja_env.filters["format_datetime"] = format_datetime

    @app.context_processor
    def inject_gst():
        """Inject the GST constant into all templates."""
        return dict(GST=GST)

    @app.context_processor
    def inject_nav_links():
        """Provide navigation labels to templates."""
        return dict(NAV_LINKS=NAV_LINKS)

    @app.context_processor
    def inject_grouped_nav_links():
        """Provide grouped navigation metadata to templates."""

        def grouped_nav_links():
            if not current_user.is_authenticated:
                return []
            visible_groups = []
            for label, links, _admin_only in NAV_GROUPS:
                visible_links = [
                    (endpoint, link_label)
                    for endpoint, link_label in links
                    if current_user.can_access_endpoint(endpoint, "GET")
                ]
                if visible_links:
                    visible_groups.append((label, visible_links))
            return visible_groups

        return {"GROUPED_NAV_LINKS": grouped_nav_links}

    @app.context_processor
    def inject_permission_helpers():
        """Expose permission helpers to templates."""

        def has_permission(code):
            return bool(
                current_user.is_authenticated
                and current_user.has_permission(code)
            )

        def can_access_endpoint(endpoint, method="GET"):
            if not current_user.is_authenticated:
                return False
            return current_user.can_access_endpoint(endpoint, method)

        def default_home_url():
            if not current_user.is_authenticated:
                return url_for("auth.login")
            if current_user.can_access_endpoint("main.home", "GET"):
                return url_for("main.home")
            return url_for(get_default_landing_endpoint(current_user))

        return {
            "has_permission": has_permission,
            "can_access_endpoint": can_access_endpoint,
            "default_home_url": default_home_url,
        }

    @app.context_processor
    def inject_safe_url_for():
        """Provide a URL helper that tolerates missing endpoints."""

        def safe_url_for(endpoint, **kwargs):
            try:
                if (
                    current_user.is_authenticated
                    and not user_can_access_endpoint(current_user, endpoint, "GET")
                ):
                    return None
                return url_for(endpoint, **kwargs)
            except BuildError:
                return None

        return {"safe_url_for": safe_url_for}

    @app.context_processor
    def inject_pagination_sizes():
        """Expose pagination size options to all templates."""
        from app.utils.pagination import PAGINATION_SIZES

        return {"PAGINATION_SIZES": PAGINATION_SIZES}

    @app.before_request
    def set_csp_nonce():
        """Generate a nonce for inline scripts allowed by the CSP."""

        g.csp_nonce = secrets.token_urlsafe(16)

    @app.after_request
    def apply_security_headers(response):
        """Attach standard security headers to every response."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.is_secure or app.config.get("ENFORCE_HTTPS", False):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        nonce = getattr(g, "csp_nonce", "")
        if not nonce:
            nonce = secrets.token_urlsafe(16)
            g.csp_nonce = nonce
        csp_template = app.config.get("CONTENT_SECURITY_POLICY_TEMPLATE")
        if csp_template is None:
            csp_template = app.config.get(
                "CONTENT_SECURITY_POLICY", DEFAULT_CSP_TEMPLATE
            )
        try:
            csp = csp_template.format(nonce=nonce)
        except Exception:
            csp = csp_template
        response.headers.setdefault("Content-Security-Policy", csp)
        return response

    @app.context_processor
    def inject_csp_nonce():
        """Expose the CSP nonce to templates for inline scripts."""

        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.before_request
    def block_http_options():
        """Return a 405 for HTTP OPTIONS requests to reduce information leakage."""
        if request.method == "OPTIONS":
            return Response(status=405)

    @app.before_request
    def enforce_endpoint_permissions():
        """Reject direct requests to endpoints the current user cannot access."""

        if not current_user.is_authenticated:
            return
        if not getattr(current_user, "active", False):
            logout_user()
            flash("Your account is no longer active. Please contact an administrator.", "warning")
            return redirect(url_for("auth.login"))
        if not user_can_access_endpoint(current_user, request.endpoint, request.method):
            return Response(status=403)

    @app.before_request
    def enforce_login_activity():
        """Log users out after inactivity and enforce periodic reauthentication."""

        if not current_user.is_authenticated:
            return

        now = datetime.utcnow()
        inactivity_limit = timedelta(days=7)
        reauth_limit = timedelta(days=30)

        last_active = current_user.last_active_at
        if last_active and now - last_active > inactivity_limit:
            logout_user()
            flash("You have been logged out due to inactivity.", "warning")
            return redirect(url_for("auth.login"))

        last_forced_login = current_user.last_forced_login_at
        if last_forced_login and now - last_forced_login > reauth_limit:
            logout_user()
            flash("Please sign in again to continue.", "warning")
            return redirect(url_for("auth.login"))

        current_user.last_active_at = now
        if current_user.last_forced_login_at is None:
            current_user.last_forced_login_at = now
        db.session.commit()

    @app.route("/.well-known/security.txt")
    def security_txt():
        """Provide contact details for responsible disclosure."""
        contact_email = os.getenv("SECURITY_CONTACT_EMAIL") or os.getenv(
            "ADMIN_EMAIL", "security@example.com"
        )
        policy_url = os.getenv("SECURITY_POLICY_URL", "https://example.com/security")
        lines = [
            f"Contact: mailto:{contact_email}",
            f"Policy: {policy_url}",
            "Preferred-Languages: en",
        ]
        return Response("\n".join(lines) + "\n", mimetype="text/plain")

    should_create_all = _should_auto_create_schema(args)

    with app.app_context():
        # Ensure models are imported and the database schema is created on
        # application start.  This allows the app to run even if migrations
        # have not been executed yet, avoiding "no such table" errors.
        from . import models  # noqa: F401

        if should_create_all:
            db.create_all()
            sync_permission_data(db.session)

        from app.routes.auth_routes import admin, auth
        from app.routes.customer_routes import customer
        from app.routes.event_routes import event
        from app.routes.glcode_routes import glcode_bp
        from app.routes.invoice_routes import invoice
        from app.routes.item_routes import item
        from app.routes.location_routes import location
        from app.routes.mailgun_routes import mailgun
        from app.routes.main_routes import main
        from app.routes.menu_routes import menu as menu_bp
        from app.routes.note_routes import notes
        from app.routes.preferences_routes import preferences
        from app.routes.product_routes import product
        from app.routes.purchase_routes import purchase
        from app.routes.report_routes import report
        from app.routes.spoilage_routes import spoilage
        from app.routes.transfer_routes import transfer
        from app.routes.vendor_routes import vendor

        app.register_blueprint(auth, url_prefix="/auth")
        app.register_blueprint(main)
        app.register_blueprint(menu_bp)
        app.register_blueprint(location)
        app.register_blueprint(item)
        app.register_blueprint(transfer)
        app.register_blueprint(spoilage)
        app.register_blueprint(admin)
        app.register_blueprint(customer)
        app.register_blueprint(invoice)
        app.register_blueprint(product)
        app.register_blueprint(notes)
        app.register_blueprint(purchase)
        app.register_blueprint(report)
        app.register_blueprint(vendor)
        app.register_blueprint(mailgun)
        app.register_blueprint(event)
        app.register_blueprint(glcode_bp)
        app.register_blueprint(preferences)
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from app.models import Setting

        try:
            setting = Setting.query.filter_by(name="GST").first()
            if setting is not None:
                GST = setting.value

            retail_price_setting = Setting.query.filter_by(
                name="RETAIL_POP_PRICE"
            ).first()
            if retail_price_setting is not None:
                RETAIL_POP_PRICE = retail_price_setting.value or "0.00"

            tz_setting = Setting.query.filter_by(name="DEFAULT_TIMEZONE").first()
            if tz_setting is not None and tz_setting.value:
                DEFAULT_TIMEZONE = tz_setting.value

            auto_setting = Setting.query.filter_by(name="AUTO_BACKUP_ENABLED").first()
            interval_value_setting = Setting.query.filter_by(
                name="AUTO_BACKUP_INTERVAL_VALUE"
            ).first()
            interval_unit_setting = Setting.query.filter_by(
                name="AUTO_BACKUP_INTERVAL_UNIT"
            ).first()
            max_backups_setting = Setting.query.filter_by(name="MAX_BACKUPS").first()
            conversions_setting = Setting.query.filter_by(
                name="BASE_UNIT_CONVERSIONS"
            ).first()

            from app.utils.backup import UNIT_SECONDS, start_auto_backup_thread
            from app.utils.units import (
                DEFAULT_BASE_UNIT_CONVERSIONS,
                parse_conversion_setting,
            )
            from app.services.pos_sales_polling import start_pos_sales_mailbox_poller

            app.config["AUTO_BACKUP_ENABLED"] = (
                auto_setting.value == "1" if auto_setting else False
            )
            app.config["AUTO_BACKUP_INTERVAL_VALUE"] = (
                int(interval_value_setting.value)
                if interval_value_setting and interval_value_setting.value
                else 1
            )
            app.config["AUTO_BACKUP_INTERVAL_UNIT"] = (
                interval_unit_setting.value
                if interval_unit_setting and interval_unit_setting.value
                else "day"
            )
            app.config["MAX_BACKUPS"] = (
                int(max_backups_setting.value)
                if max_backups_setting and max_backups_setting.value
                else 5
            )
            app.config["RETAIL_POP_PRICE"] = RETAIL_POP_PRICE
            if conversions_setting is not None and conversions_setting.value:
                BASE_UNIT_CONVERSIONS = parse_conversion_setting(
                    conversions_setting.value
                )
            else:
                BASE_UNIT_CONVERSIONS = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
            app.config["BASE_UNIT_CONVERSIONS"] = BASE_UNIT_CONVERSIONS
            app.config["AUTO_BACKUP_INTERVAL"] = (
                app.config["AUTO_BACKUP_INTERVAL_VALUE"]
                * UNIT_SECONDS[app.config["AUTO_BACKUP_INTERVAL_UNIT"]]
            )
            start_auto_backup_thread(app)
            start_pos_sales_mailbox_poller(app)
        except (OperationalError, ProgrammingError):
            pass

        csrf_protect = CSRFProtect(app)
        csrf_protect.exempt(mailgun)

        @app.errorhandler(CSRFError)
        def handle_csrf_error(error):
            """Render a helpful page when CSRF validation fails."""
            return (
                render_template(
                    "errors/csrf_error.html",
                    reason=error.description,
                ),
                400,
            )

        @app.errorhandler(RequestEntityTooLarge)
        def handle_request_entity_too_large(error):
            """Render a clear error for oversized uploads."""
            if request.path.startswith("/webhooks/mailgun"):
                return (
                    jsonify({"ok": False, "error": "payload_too_large"}),
                    413,
                )
            return (
                render_template(
                    "errors/upload_too_large.html",
                    max_upload_size_mb=max(
                        1,
                        int(
                            app.config.get(
                                "MAX_UPLOAD_FILE_SIZE_BYTES",
                                DEFAULT_MAX_UPLOAD_FILE_SIZE_BYTES,
                            )
                        )
                        // (1024 * 1024),
                    ),
                ),
                413,
            )

        @app.errorhandler(Exception)
        def handle_unhandled_exception(error):
            """Render safe internal-error details without masking HTTP status codes."""
            if isinstance(error, HTTPException):
                return error

            error_token = secrets.token_hex(8)
            traceback_text = traceback.format_exc()
            if traceback_text.strip() in {"", "NoneType: None"}:
                traceback_text = "".join(
                    traceback.format_exception(
                        type(error),
                        error,
                        error.__traceback__,
                    )
                )
            user_identity = "anonymous"
            cached_user_id = None
            try:
                get_id = getattr(current_user, "get_id", None)
                if callable(get_id):
                    cached_user_id = get_id()
            except Exception:
                pass

            try:
                if cached_user_id is not None:
                    user_identity = str(cached_user_id)
                session_needs_rollback = getattr(db.session, "is_active", True) is False
            except Exception:
                session_needs_rollback = False

            if session_needs_rollback:
                try:
                    db.session.rollback()
                except Exception:
                    pass

            try:
                if getattr(current_user, "is_authenticated", False):
                    user_id = cached_user_id
                    user_email = None
                    if user_id is None:
                        user_id = getattr(current_user, "id", None)
                    user_email = getattr(current_user, "email", None)
                    if user_id is not None and user_email:
                        user_identity = f"{user_id}:{user_email}"
                    elif user_id is not None:
                        user_identity = str(user_id)
                    elif user_email:
                        user_identity = user_email
            except PendingRollbackError:
                user_identity = (
                    str(cached_user_id)
                    if cached_user_id is not None
                    else "anonymous"
                )
            except Exception:
                user_identity = (
                    str(cached_user_id)
                    if cached_user_id is not None
                    else "anonymous"
                )

            try:
                app.logger.error(
                    "Unhandled exception",
                    extra={
                        "error_token": error_token,
                        "request_path": request.path,
                        "request_method": request.method,
                        "user_identity": user_identity,
                        "remote_addr": request.headers.get(
                            "X-Forwarded-For", request.remote_addr or "-"
                        ),
                        "user_agent": str(request.user_agent),
                        "traceback_text": traceback_text,
                    },
                )
            except Exception:
                pass
            error_details = _build_user_error_details(
                traceback_text,
                show_detailed_trace=app.config.get(
                    "SHOW_ERROR_DETAILS_TO_USERS", False
                ),
                max_length=int(app.config.get("ERROR_DETAILS_MAX_LENGTH", 8000)),
                error_token=error_token,
            )
            try:
                return (
                    render_template(
                        "errors/internal_error.html",
                        error_token=error_token,
                        error_details=error_details,
                        show_error_details=app.config.get(
                            "SHOW_ERROR_DETAILS_TO_USERS", False
                        ),
                    ),
                    500,
                )
            except Exception:
                return (
                    "An internal error occurred. "
                    f"Reference token: {error_token}",
                    500,
                )

    return app, socketio
