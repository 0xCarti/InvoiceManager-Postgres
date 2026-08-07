from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.permission_helpers import grant_item_workflow_permissions
from tests.utils import login


def _prepare_items(app):
    with app.app_context():
        user = User(
            email="forecast-link@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        db.session.add(user)
        db.session.add(Item(name="Widget", base_unit="each"))
        db.session.commit()
        grant_item_workflow_permissions(user)
        return user.email


def test_purchase_orders_page_owns_forecast_report_link(client, app):
    email = _prepare_items(app)

    with client:
        login(client, email, "pass")

        items_response = client.get("/items")
        purchase_orders_response = client.get("/purchase_orders")

        assert items_response.status_code == 200
        assert b"/reports/purchase-cost-forecast" not in items_response.data
        assert purchase_orders_response.status_code == 200
        assert b"/reports/purchase-cost-forecast" in purchase_orders_response.data
        assert b"Forecast Purchase Costs" in purchase_orders_response.data
