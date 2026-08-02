from tests.test_location_routes import setup_data

from app import db
from app.models import (
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
)
from app.routes.location_routes import _protected_location_item_ids
from tests.utils import login


def test_menu_edit_page_renders_selected_only_toggle(client, app):
    email, _, menu_id = setup_data(app)

    with client:
        login(client, email, "pass")
        response = client.get(f"/menus/{menu_id}/edit", follow_redirects=True)

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="product-show-selected-toggle"' in body
    assert "Show Selected Only" in body


def test_menu_edit_syncs_location_stand_sheet(client, app):
    email, prod1_id, menu_id = setup_data(app)
    with app.app_context():
        menu = db.session.get(Menu, menu_id)
        assert menu is not None
        flour_item = Item.query.filter_by(name="Flour").first()
        assert flour_item is not None
        gl_code_id = flour_item.purchase_gl_code_id
        sugar = Item(name="Sugar", base_unit="gram", purchase_gl_code_id=gl_code_id)
        db.session.add(sugar)
        db.session.flush()
        sugar_id = sugar.id
        sugar_unit = ItemUnit(
            item_id=sugar.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        cookie = Product(name="Cookie", price=3.0, cost=1.0)
        db.session.add_all([sugar_unit, cookie])
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=cookie.id,
                item_id=sugar.id,
                unit_id=sugar_unit.id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        prod2_id = cookie.id
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/locations/add",
            data={"name": "Bakery", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        location = Location.query.filter_by(name="Bakery").first()
        assert location is not None
        location_id = location.id
        assert LocationStandItem.query.filter_by(location_id=location_id).count() == 1
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/menus/{menu_id}/edit",
            data={
                "name": "Bakery Regular",
                "description": "Default offerings",
                "product_ids": [str(prod1_id), str(prod2_id)],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        stand_items = LocationStandItem.query.filter_by(location_id=location_id).all()
        assert len(stand_items) == 2
        assert {item.item.name for item in stand_items} == {"Flour", "Sugar"}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/menus/{menu_id}/edit",
            data={
                "name": "Bakery Regular",
                "description": "Default offerings",
                "product_ids": [str(prod2_id)],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        location = db.session.get(Location, location_id)
        assert location is not None
        stand_items = LocationStandItem.query.filter_by(location_id=location_id).all()
        assert len(stand_items) == 2
        assert _protected_location_item_ids(location) == {sugar_id}
