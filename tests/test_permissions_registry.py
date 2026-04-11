from types import SimpleNamespace

from flask import Flask

from app.permissions import (
    get_default_landing_endpoint,
    get_permission_categories,
    user_can_access_endpoint,
)


class DummyUser:
    def __init__(self, *permissions, is_super_admin=False, is_authenticated=True):
        self._permissions = set(permissions)
        self.is_super_admin = is_super_admin
        self.is_authenticated = is_authenticated

    def has_permission(self, code: str) -> bool:
        return code in self._permissions


def test_user_can_access_endpoint_requires_matching_permission():
    user = DummyUser("purchase_orders.view")

    assert user_can_access_endpoint(user, "purchase.view_purchase_orders")
    assert not user_can_access_endpoint(user, "purchase.create_purchase_order")
    assert not user_can_access_endpoint(user, "main.metabase_redirect")
    assert user_can_access_endpoint(
        DummyUser("reports.metabase"), "main.metabase_redirect"
    )
    assert not user_can_access_endpoint(
        DummyUser("dashboard.view"), "main.add_metabase_card", "POST"
    )
    assert user_can_access_endpoint(
        DummyUser("dashboard.view", "reports.metabase"),
        "main.add_metabase_card",
        "POST",
    )


def test_super_admin_bypasses_endpoint_permission_checks():
    user = DummyUser(is_super_admin=True)

    assert user_can_access_endpoint(user, "admin.settings")
    assert user_can_access_endpoint(user, "admin.sales_import_detail", "POST")


def test_default_landing_endpoint_prefers_first_accessible_route():
    app = Flask(__name__)
    app.view_functions.update(
        {
            "transfer.view_transfers": SimpleNamespace(),
            "main.home": SimpleNamespace(),
            "admin.users": SimpleNamespace(),
            "auth.profile": SimpleNamespace(),
        }
    )

    with app.app_context():
        assert get_default_landing_endpoint(DummyUser("transfers.view")) == (
            "transfer.view_transfers"
        )
        assert get_default_landing_endpoint(DummyUser("dashboard.view")) == "main.home"
        assert get_default_landing_endpoint(DummyUser("users.view")) == "admin.users"
        assert get_default_landing_endpoint(DummyUser()) == "auth.profile"


def test_permission_categories_include_system_admin_section():
    categories = get_permission_categories()
    labels = {category["label"] for category in categories}

    assert "Transfers" in labels
    assert "Permission Groups" in labels
    assert "Permissions" in labels
