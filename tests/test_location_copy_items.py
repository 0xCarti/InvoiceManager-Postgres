from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
    User,
)
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="copy@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        gl = GLCode.query.first()
        item = Item(
            name="Sugar",
            base_unit="gram",
            purchase_gl_code_id=gl.id,
        )
        db.session.add_all([user, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        product = Product(name="Candy", price=1.0, cost=0.5)
        db.session.add_all([unit, product])
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        menu = Menu(name="Copy Menu", description="Single product menu")
        menu.products.append(product)
        db.session.add(menu)
        db.session.commit()
        return user.email, product.id, menu.id


def setup_multi_product_data(app):
    """Create two products that share the same item."""
    with app.app_context():
        user = User(
            email="copy2@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        gl = GLCode.query.first()
        item = Item(
            name="Sugar",
            base_unit="gram",
            purchase_gl_code_id=gl.id,
        )
        db.session.add_all([user, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        prod1 = Product(name="Candy", price=1.0, cost=0.5)
        prod2 = Product(name="Cookie", price=1.5, cost=0.7)
        db.session.add_all([unit, prod1, prod2])
        db.session.commit()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=prod1.id,
                    item_id=item.id,
                    unit_id=unit.id,
                    quantity=1,
                    countable=True,
                ),
                ProductRecipeItem(
                    product_id=prod2.id,
                    item_id=item.id,
                    unit_id=unit.id,
                    quantity=2,
                    countable=True,
                ),
            ]
        )
        db.session.commit()
        menu = Menu(name="Copy Menu Multi", description="Two product menu")
        menu.products.extend([prod1, prod2])
        db.session.add(menu)
        db.session.commit()
        return user.email, [prod1.id, prod2.id], menu.id


def test_copy_location_items(client, app):
    email, prod_id, menu_id = setup_data(app)
    with client:
        login(client, email, "pass")
        # create source location with product
        resp = client.post(
            "/locations/add",
            data={"name": "Source", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # create target location without products
        resp = client.post(
            "/locations/add",
            data={"name": "Target", "menu_id": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            source = Location.query.filter_by(name="Source").first()
            target = Location.query.filter_by(name="Target").first()
            assert source and target
            source_id = source.id
            target_id = target.id
        # copy items
        resp = client.post(
            f"/locations/{source_id}/copy_items",
            json={"target_id": target_id},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # verify target location now has product and stand item
        with app.app_context():
            refreshed = db.session.get(Location, target_id)
            assert len(refreshed.products) == 1
            assert refreshed.current_menu_id == menu_id
            assert (
                LocationStandItem.query.filter_by(location_id=target_id).count()
                == 1
            )


def test_copy_button_visible(client, app):
    email, prod_id, menu_id = setup_data(app)
    with client:
        login(client, email, "pass")
        # create a location so the listing renders at least one row
        resp = client.post(
            "/locations/add",
            data={"name": "Source", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        resp = client.get("/locations")
        assert b"Copy Stand Sheet" in resp.data


def test_copy_location_items_multiple_targets(client, app):
    """Copy stand items to multiple targets without duplicates."""
    email, prod_ids, menu_id = setup_multi_product_data(app)
    with client:
        login(client, email, "pass")
        # create source location with both products
        resp = client.post(
            "/locations/add",
            data={"name": "Source", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # create two target locations without products
        resp = client.post(
            "/locations/add",
            data={"name": "Target1", "menu_id": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        resp = client.post(
            "/locations/add",
            data={"name": "Target2", "menu_id": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            source = Location.query.filter_by(name="Source").first()
            t1 = Location.query.filter_by(name="Target1").first()
            t2 = Location.query.filter_by(name="Target2").first()
            source_id = source.id
            t1_id = t1.id
            t2_id = t2.id
        # copy items to both targets
        resp = client.post(
            f"/locations/{source_id}/copy_items",
            json={"target_ids": [t1_id, t2_id]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        # both targets should have two products and one stand item each
        with app.app_context():
            refreshed1 = db.session.get(Location, t1_id)
            refreshed2 = db.session.get(Location, t2_id)
            assert refreshed1.current_menu_id == menu_id
            assert refreshed2.current_menu_id == menu_id
            assert len(refreshed1.products) == 2
            assert len(refreshed2.products) == 2
            assert LocationStandItem.query.filter_by(location_id=t1_id).count() == 1
            assert LocationStandItem.query.filter_by(location_id=t2_id).count() == 1
