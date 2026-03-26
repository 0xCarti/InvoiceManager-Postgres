from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.utils import login


def _prepare_items(app):
    with app.app_context():
        user = User(
            email="forecast-link@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.add(Item(name="Widget", base_unit="each"))
        db.session.commit()
        return user.email


def test_items_page_includes_forecast_report_link(client, app):
    email = _prepare_items(app)

    with client:
        login(client, email, "pass")

        resp = client.get("/items")

        assert resp.status_code == 200
        assert b"/reports/purchase-cost-forecast" in resp.data
        assert b"Forecasted Stock Item Sales" in resp.data
