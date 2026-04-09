import csv
import io

from app import db
from app.models import Product, Setting
from tests.test_location_routes import setup_data


def test_menu_feed_requires_valid_token(client, app):
    setup_data(app)
    with app.app_context():
        app.config["MENU_FEED_API_TOKEN"] = "menu-feed-secret"

    missing_token_response = client.get("/integrations/menu-feed")
    invalid_token_response = client.get("/integrations/menu-feed?token=wrong-token")

    assert missing_token_response.status_code == 403
    assert invalid_token_response.status_code == 403


def test_menu_feed_returns_json_rows_for_all_products(client, app):
    _, product_id, _ = setup_data(app)
    with app.app_context():
        app.config["MENU_FEED_API_TOKEN"] = "menu-feed-secret"

    response = client.get("/integrations/menu-feed.json?token=menu-feed-secret")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["products"] == [
        {
            "id": str(product_id),
            "name": "Cake",
            "category": "",
            "image_url": "",
            "description": "",
            "enabled": 1,
            "price": 5.0,
        }
    ]


def test_menu_feed_csv_returns_all_products(client, app):
    setup_data(app)
    with app.app_context():
        app.config["MENU_FEED_API_TOKEN"] = "menu-feed-secret"
        db.session.add(Product(name="Brownie", price=3.25, cost=1.1))
        db.session.commit()

    response = client.get("/integrations/menu-feed.csv?token=menu-feed-secret")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert len(rows) == 2
    row_names = [row["name"] for row in rows]
    assert row_names == ["Brownie", "Cake"]
    assert rows[0]["category"] == ""
    assert rows[0]["price"] == "3.25"
    assert "calories" not in rows[0]


def test_menu_feed_uses_setting_token_when_env_token_is_empty(client, app):
    setup_data(app)
    with app.app_context():
        app.config["MENU_FEED_API_TOKEN"] = ""
        Setting.set_menu_feed_api_token("setting-token")
        db.session.commit()

    response = client.get("/integrations/menu-feed?token=setting-token")
    assert response.status_code == 200
