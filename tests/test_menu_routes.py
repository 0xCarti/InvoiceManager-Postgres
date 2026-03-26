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
from tests.utils import login


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
        assert LocationStandItem.query.filter_by(location_id=location.id).count() == 1
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
        stand_items = LocationStandItem.query.filter_by(location_id=location.id).all()
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
        stand_items = LocationStandItem.query.filter_by(location_id=location.id).all()
        assert len(stand_items) == 1
        assert stand_items[0].item.name == "Sugar"
