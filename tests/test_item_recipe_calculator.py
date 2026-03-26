import os

from app import db
from app.models import Item, ItemUnit
from tests.utils import login


def _ensure_sample_item():
    item = Item.query.filter_by(name="Recipe Calculator Test Item").first()
    if not item:
        item = Item(name="Recipe Calculator Test Item", base_unit="each", cost=2.5)
        db.session.add(item)
        db.session.flush()
    if not ItemUnit.query.filter_by(item_id=item.id, name=item.base_unit).first():
        db.session.add(ItemUnit(item_id=item.id, name=item.base_unit, factor=1.0))
    if not ItemUnit.query.filter_by(item_id=item.id, name="case").first():
        db.session.add(ItemUnit(item_id=item.id, name="case", factor=12.0))
    db.session.commit()
    return item


def test_recipe_calculator_page_renders(client, app):
    with app.app_context():
        _ensure_sample_item()

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    client.environ_base["wsgi.url_scheme"] = "https"
    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        response = client.get("/items/recipe-cost-calculator", follow_redirects=True)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "Recipe Cost Calculator" in page
        assert "Base Cost per Product" in page
        assert "Recipe Calculator Test Item" in page


def test_items_page_links_to_recipe_calculator(client, app):
    with app.app_context():
        _ensure_sample_item()

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    client.environ_base["wsgi.url_scheme"] = "https"
    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        response = client.get("/items", follow_redirects=True)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "/items/recipe-cost-calculator" in page
        assert "Recipe Cost Calculator" in page
