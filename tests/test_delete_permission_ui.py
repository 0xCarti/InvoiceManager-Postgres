from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, GLCode, Item, Location, LocationStandItem, Menu, Product, User, Vendor
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _setup_view_only_delete_data(app):
    with app.app_context():
        user = User(
            email="delete-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        customer = Customer(first_name="Delete", last_name="Customer")
        vendor = Vendor(first_name="Delete", last_name="Vendor")
        gl_code = GLCode(code="D100", description="Delete test code")
        menu = Menu(name="Delete Test Menu")
        location = Location(name="Delete Test Location")
        item = Item(
            name="Delete Test Item",
            base_unit="each",
            quantity=5.0,
            cost=1.5,
        )
        product = Product(
            name="Delete Test Product",
            price=5.0,
            cost=2.0,
            quantity=3.0,
        )

        db.session.add_all([user, customer, vendor, gl_code, menu, location, item, product])
        db.session.flush()
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=1.0,
            )
        )
        db.session.commit()

        grant_permissions(
            user,
            "customers.view",
            "vendors.view",
            "gl_codes.view",
            "menus.view",
            "products.view",
            "items.view",
            "locations.view",
            group_name="Delete View Only",
            description="View-only permissions for delete-button visibility tests.",
        )

        return {
            "email": user.email,
            "customer_id": customer.id,
            "vendor_id": vendor.id,
            "gl_code_id": gl_code.id,
            "menu_id": menu.id,
            "product_id": product.id,
            "location_id": location.id,
            "item_id": item.id,
        }


def test_view_only_pages_hide_delete_actions(client, app):
    data = _setup_view_only_delete_data(app)

    with client:
        login(client, data["email"], "pass")

        page_checks = [
            ("/customers", f"/customers/{data['customer_id']}/delete"),
            ("/vendors", f"/vendors/{data['vendor_id']}/delete"),
            ("/gl_codes", f"/gl_codes/{data['gl_code_id']}/delete"),
            ("/menus", f"/menus/{data['menu_id']}/delete"),
            ("/products", f"/products/{data['product_id']}/delete"),
            ("/locations", f"/locations/delete/{data['location_id']}"),
        ]

        for path, forbidden_action in page_checks:
            response = client.get(path, follow_redirects=True)
            assert response.status_code == 200
            assert forbidden_action.encode("utf-8") not in response.data

        items_page = client.get("/items", follow_redirects=True)
        assert items_page.status_code == 200
        assert b"Delete Items" not in items_page.data

        location_items_page = client.get(
            f"/locations/{data['location_id']}/items",
            follow_redirects=True,
        )
        assert location_items_page.status_code == 200
        assert (
            f"/locations/{data['location_id']}/items/{data['item_id']}/delete".encode(
                "utf-8"
            )
            not in location_items_page.data
        )


def test_permission_denied_xhr_returns_json_payload(client, app):
    with app.app_context():
        user = User(
            email="xhr-no-perms@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()

    with client:
        login(client, "xhr-no-perms@example.com", "pass")
        response = client.get(
            "/purchase_orders",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    assert response.status_code == 403
    assert response.is_json
    assert response.get_json() == {"ok": False, "error": "forbidden"}
