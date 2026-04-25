import re

from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, GLCode, Item, Location, Menu, Product, User, Vendor
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _strip_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)


def _setup_view_only_list_data(app):
    with app.app_context():
        user = User(
            email="list-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        customer = Customer(first_name="List", last_name="Customer")
        vendor = Vendor(first_name="List", last_name="Vendor")
        menu = Menu(name="List Menu")
        gl_code = GLCode(code="L100", description="List test code")
        item = Item(name="List Item", base_unit="each", quantity=3.0, cost=1.0)
        location = Location(name="List Location")
        product = Product(name="List Product", price=9.0, cost=4.0, quantity=8.0)

        db.session.add_all([user, customer, vendor, menu, gl_code, item, location, product])
        db.session.flush()
        product.menus.append(menu)
        product.locations.append(location)
        db.session.commit()

        grant_permissions(
            user,
            "customers.view",
            "vendors.view",
            "menus.view",
            "gl_codes.view",
            "items.view",
            "locations.view",
            "products.view",
            group_name="List View Only",
            description="View-only permissions for list-page permission tests.",
        )

        return {
            "email": user.email,
            "customer_id": customer.id,
            "vendor_id": vendor.id,
            "menu_id": menu.id,
            "gl_code_id": gl_code.id,
            "item_id": item.id,
            "location_id": location.id,
            "product_id": product.id,
        }


def test_list_pages_hide_create_edit_bulk_and_report_controls(client, app):
    data = _setup_view_only_list_data(app)

    with app.test_request_context():
        customer_edit_url = f"/customers/{data['customer_id']}/edit"
        customer_delete_url = f"/customers/{data['customer_id']}/delete"
        vendor_edit_url = f"/vendors/{data['vendor_id']}/edit"
        menu_edit_url = f"/menus/{data['menu_id']}/edit"
        menu_assign_url = f"/menus/{data['menu_id']}/assign"
        item_edit_url = f"/items/{data['item_id']}/edit"
        item_copy_url = f"/items/{data['item_id']}/copy"
        item_locations_url = f"/items/{data['item_id']}/locations"
        location_edit_url = f"/locations/edit/{data['location_id']}"
        location_items_url = f"/locations/{data['location_id']}/items"
        product_edit_url = f"/products/{data['product_id']}/edit"
        product_copy_url = f"/products/{data['product_id']}/copy"

    with client:
        login(client, data["email"], "pass")

        page_expectations = [
            ("/customers", ["data-bs-target=\"#createCustomerModal\"", customer_edit_url, customer_delete_url]),
            ("/vendors", ["id=\"createVendorBtn\"", vendor_edit_url, "vendorModal"]),
            ("/menus", ["Create Menu", menu_edit_url, menu_assign_url]),
            ("/items", ["id=\"createItemBtn\"", "id=\"bulkEditItemsBtn\"", "id=\"select-all\"", "Import Items", "Delete Items", item_edit_url, item_copy_url, "id=\"itemModal\"", "id=\"itemBulkModal\""]),
            ("/locations", ["id=\"addLocationBtn\"", "id=\"bulkEditLocationsBtn\"", "id=\"emailStandSheetsBtn\"", "id=\"select-all-locations\"", "id=\"locationModal\"", "id=\"locationBulkModal\"", "id=\"copyModal\"", location_edit_url]),
            ("/products", ["data-bs-target=\"#createProductModal\"", "id=\"editProductModal\"", "id=\"productBulkModal\"", "id=\"bulkEditProductsBtn\"", "id=\"select-all-products\"", "Set Cost From Recipe", "Recipe Report", "Revenue Report", "Stock Usage Report", "Department Sales Forecast", product_edit_url, product_copy_url, "/menus/edit/", "/locations/edit/"]),
            ("/gl_codes", ["id=\"glCodeModal\"", "id=\"add-gl-code\"", "edit-gl-code"]),
        ]

        for path, forbidden_strings in page_expectations:
            response = client.get(path, follow_redirects=True)
            assert response.status_code == 200
            html = _strip_scripts(response.get_data(as_text=True))
            for forbidden in forbidden_strings:
                assert forbidden not in html


def test_js_generated_row_markup_is_permission_gated(client, app):
    data = _setup_view_only_list_data(app)

    with client:
        login(client, data["email"], "pass")

        gl_codes_html = client.get("/gl_codes", follow_redirects=True).get_data(as_text=True)
        assert "edit-gl-code" not in gl_codes_html
        assert "glCodeModal" not in gl_codes_html

        locations_html = client.get("/locations", follow_redirects=True).get_data(as_text=True)
        assert "edit-location-btn" not in locations_html
        assert "copy-location-btn" not in locations_html
        assert 'name=\\"location_ids\\"' not in locations_html

        products_html = _strip_scripts(client.get("/products", follow_redirects=True).get_data(as_text=True))
        assert "/menus/edit/" not in products_html
        assert "/locations/edit/" not in products_html
        assert "edit-product-link" not in products_html
        assert "productBulkModal" not in products_html
