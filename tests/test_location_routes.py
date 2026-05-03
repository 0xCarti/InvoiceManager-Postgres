from datetime import date, datetime
from contextlib import contextmanager

from flask import template_rendered
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    PosSalesImport,
    PosSalesImportLocation,
    Product,
    ProductRecipeItem,
    TerminalSaleLocationAlias,
    Transfer,
    User,
)
from tests.permission_helpers import grant_permissions
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="loc@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        gl = (
            GLCode.query.filter(GLCode.code.like("5%"))
            .order_by(GLCode.id)
            .first()
            or GLCode.query.filter(GLCode.code.like("6%"))
            .order_by(GLCode.id)
            .first()
            or GLCode.query.first()
        )
        if gl is None or not str(gl.code or "").startswith(("5", "6")):
            gl = GLCode(code="5000")
            db.session.add(gl)
            db.session.flush()
        item = Item(
            name="Flour",
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
        product = Product(name="Cake", price=5.0, cost=2.0)
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
        menu = Menu(name="Bakery Regular", description="Default offerings")
        menu.products.append(product)
        db.session.add(menu)
        db.session.commit()
        return user.email, product.id, menu.id


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def test_location_flow(client, app):
    email, prod_id, menu_id = setup_data(app)
    with client:
        login(client, email, "pass")
        assert client.get("/locations/add").status_code == 200
        resp = client.post(
            "/locations/add",
            data={"name": "Kitchen", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        loc = Location.query.filter_by(name="Kitchen").first()
        assert loc is not None
        lid = loc.id
        assert LocationStandItem.query.filter_by(location_id=lid).count() == 1
        # second product for edit test
        prod2 = Product(name="Pie", price=4.0, cost=2.0)
        db.session.add(prod2)
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=prod2.id,
                item_id=Item.query.first().id,
                unit_id=ItemUnit.query.first().id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        expanded_menu = Menu(name="Bakery Expanded", description="With pie")
        expanded_menu.products.extend([
            Product.query.get(prod_id),
            prod2,
        ])
        db.session.add(expanded_menu)
        db.session.commit()
        expanded_menu_id = expanded_menu.id
    with client:
        login(client, email, "pass")
        resp = client.get("/locations")
        assert resp.status_code == 200
        assert b"app-page-shell" in resp.data
        resp = client.get(f"/locations/{lid}/stand_sheet")
        assert resp.status_code == 200
        assert b"Location: Kitchen" in resp.data
        assert b"Date Used" in resp.data
        resp = client.post(
            f"/locations/edit/{lid}",
            data={"name": "Kitchen2", "menu_id": str(expanded_menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        loc = db.session.get(Location, lid)
        assert loc.current_menu_id == expanded_menu_id
        stand_items = LocationStandItem.query.filter_by(location_id=lid).all()
        assert len(stand_items) == 1
        assert stand_items[0].item_id == Item.query.first().id
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/locations/delete/{lid}",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert client.get("/locations/edit/999").status_code == 404
        assert client.get("/locations/999/stand_sheet").status_code == 404
        assert client.post("/locations/delete/999").status_code == 404
    with app.app_context():
        loc = db.session.get(Location, lid)
        assert loc.archived


def test_edit_location_without_menu_preserves_products(client, app):
    email, product_id, _ = setup_data(app)
    with app.app_context():
        product = db.session.get(Product, product_id)
        location = Location(name="Standalone")
        location.products.append(product)
        db.session.add(location)
        db.session.flush()
        recipe_item = product.recipe_items[0]
        expected_item_id = recipe_item.item_id
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=expected_item_id,
                expected_count=5,
                purchase_gl_code_id=recipe_item.item.purchase_gl_code_id,
            )
        )
        db.session.commit()
        location_id = location.id

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/locations/edit/{location_id}",
            data={
                "name": "Standalone Updated",
                "menu_id": "0",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        location = db.session.get(Location, location_id)
        assert location.current_menu_id is None
        assert [product.id for product in location.products] == [product_id]
        stand_items = LocationStandItem.query.filter_by(location_id=location_id).all()
        assert len(stand_items) == 1
        assert stand_items[0].item_id == expected_item_id


def test_email_stand_sheet_success(monkeypatch, client, app):
    with app.app_context():
        location = Location(name="Emailable")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    sent_email = {}
    monkeypatch.setattr(
        "app.routes.location_routes.render_stand_sheet_pdf",
        lambda templates, *, base_url=None: b"PDF",
    )

    def fake_send_email(**kwargs):
        sent_email.update(kwargs)

    monkeypatch.setattr(
        "app.routes.location_routes.send_email", fake_send_email
    )

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/locations/stand_sheets/email",
            data={"email": "dest@example.com", "location_ids": str(location_id)},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert sent_email["to_address"] == "dest@example.com"
    assert b"Stand sheet sent to dest@example.com." in response.data


def test_email_multiple_stand_sheets(monkeypatch, client, app):
    with app.app_context():
        location_one = Location(name="First")
        location_two = Location(name="Second")
        db.session.add_all([location_one, location_two])
        db.session.commit()

        first_id = location_one.id
        second_id = location_two.id

    sent_email = {}
    monkeypatch.setattr(
        "app.routes.location_routes.render_stand_sheet_pdf",
        lambda templates, *, base_url=None: b"PDF",
    )

    def fake_send_email(**kwargs):
        sent_email.update(kwargs)

    monkeypatch.setattr(
        "app.routes.location_routes.send_email", fake_send_email
    )

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/locations/stand_sheets/email",
            data={
                "email": "dest@example.com",
                "location_ids": [str(first_id), str(second_id)],
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert sent_email["subject"] == "Stand sheets"
    assert sent_email["attachments"][0][0] == "stand-sheets.pdf"
    assert b"Stand sheets sent to dest@example.com." in response.data


def test_email_stand_sheet_missing_configuration(monkeypatch, client, app):
    with app.app_context():
        location = Location(name="No SMTP")
        db.session.add(location)
        db.session.commit()
        location_id = location.id

    monkeypatch.setattr(
        "app.routes.location_routes.render_stand_sheet_pdf",
        lambda templates, *, base_url=None: b"PDF",
    )

    def fake_send_email(**kwargs):
        from app.utils.email import SMTPConfigurationError

        raise SMTPConfigurationError(["SMTP_HOST"])

    monkeypatch.setattr(
        "app.routes.location_routes.send_email", fake_send_email
    )

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/locations/stand_sheets/email",
            data={"email": "dest@example.com", "location_ids": str(location_id)},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Email settings are not configured." in response.data


def test_view_location_shows_recent_activity_and_terminal_mappings(client, app):
    email, _, menu_id = setup_data(app)

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None
        menu = db.session.get(Menu, menu_id)
        location = Location(name="Detail Stand", current_menu=menu)
        warehouse = Location(name="Warehouse")
        event = Event(
            name="Summer Festival",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 3),
        )
        db.session.add_all([location, warehouse, event])
        db.session.flush()

        db.session.add(
            TerminalSaleLocationAlias(
                source_name="Stand #1",
                normalized_name="stand_1_detail",
                location_id=location.id,
            )
        )
        db.session.add(
            Transfer(
                from_location_id=warehouse.id,
                to_location_id=location.id,
                user_id=user.id,
                date_created=datetime(2026, 7, 4, 12, 30),
                completed=True,
                from_location_name=warehouse.name,
                to_location_name=location.name,
            )
        )
        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
            confirmed=True,
        )
        db.session.add(event_location)
        db.session.flush()

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-location-detail",
            attachment_filename="detail-sales.csv",
            attachment_sha256="d" * 64,
            sales_date=date(2026, 7, 4),
            received_at=datetime(2026, 7, 5, 9, 15),
            status=PosSalesImport.STATUS_APPROVED,
        )
        db.session.add(sales_import)
        db.session.flush()
        db.session.add(
            PosSalesImportLocation(
                import_id=sales_import.id,
                source_location_name="Stand #1",
                normalized_location_name="stand_1_detail",
                location_id=location.id,
                event_location_id=event_location.id,
                total_quantity=14,
                computed_total=112,
                parse_index=0,
            )
        )
        db.session.commit()
        location_id = location.id

    with client:
        login(client, email, "pass")
        response = client.get(f"/locations/{location_id}")

    assert response.status_code == 200
    assert b"Detail Stand" in response.data
    assert b"Terminal Sales Mappings" in response.data
    assert b"Stand #1" in response.data
    assert b"Recent Transfers" in response.data
    assert b"Recent Events" in response.data
    assert b"Recent Imported Sales" in response.data
    assert b"Summer Festival" in response.data
    assert b"Cake" in response.data
    assert b"Transfer #" in response.data
    assert b"Import #" in response.data


def test_view_locations_filters_by_menu_and_spoilage(client, app):
    email, _, base_menu_id = setup_data(app)
    with app.app_context():
        menu_primary = db.session.get(Menu, base_menu_id)
        menu_secondary = Menu(name="FilterTest Menu B", description="Secondary options")
        db.session.add(menu_secondary)
        db.session.commit()
        loc_with_menu = Location(
            name="FilterTest Alpha",
            current_menu=menu_primary,
            is_spoilage=False,
        )
        loc_spoilage = Location(
            name="FilterTest Beta",
            current_menu=menu_secondary,
            is_spoilage=True,
        )
        loc_no_menu = Location(
            name="FilterTest Gamma",
            is_spoilage=False,
        )
        db.session.add_all([loc_with_menu, loc_spoilage, loc_no_menu])
        db.session.commit()
        menu_primary_id = menu_primary.id
        menu_secondary_id = menu_secondary.id

    def fetch_context(query_params):
        with captured_templates(app) as templates:
            response = client.get("/locations", query_string=query_params)
            assert response.status_code == 200
        assert templates
        return templates[-1][1]

    with client:
        login(client, email, "pass")

        base_query = [
            ("name_query", "FilterTest"),
            ("match_mode", "startswith"),
        ]

        context = fetch_context(base_query + [("menu_ids", str(menu_primary_id))])
        names = [location.name for location in context["locations"].items]
        assert names == ["FilterTest Alpha"]
        assert context["selected_menu_ids"] == {menu_primary_id}
        assert context["include_no_menu"] is False

        context = fetch_context(base_query + [("menu_ids", "0")])
        names = [location.name for location in context["locations"].items]
        assert names == ["FilterTest Gamma"]
        assert context["selected_menu_ids"] == set()
        assert context["include_no_menu"] is True

        context = fetch_context(
            base_query
            + [("menu_ids", str(menu_primary_id)), ("menu_ids", "0")]
        )
        names = {location.name for location in context["locations"].items}
        assert names == {"FilterTest Alpha", "FilterTest Gamma"}
        assert context["selected_menu_ids"] == {menu_primary_id}
        assert context["include_no_menu"] is True

        context = fetch_context(base_query + [("spoilage", "spoilage")])
        names = [location.name for location in context["locations"].items]
        assert names == ["FilterTest Beta"]
        assert context["spoilage_filter"] == "spoilage"

        context = fetch_context(base_query + [("spoilage", "non_spoilage")])
        names = {location.name for location in context["locations"].items}
        assert names == {"FilterTest Alpha", "FilterTest Gamma"}
        assert context["spoilage_filter"] == "non_spoilage"

        context = fetch_context(
            base_query
            + [
                ("menu_ids", str(menu_secondary_id)),
                ("spoilage", "spoilage"),
            ]
        )
        names = [location.name for location in context["locations"].items]
        assert names == ["FilterTest Beta"]


def test_location_filters(client, app):
    email, *_ = setup_data(app)
    with app.app_context():
        active = Location(name="ActiveLoc")
        archived_loc = Location(name="OldLoc", archived=True)
        db.session.add_all([active, archived_loc])
        db.session.commit()
    with client:
        login(client, email, "pass")
        resp = client.get("/locations")
        assert b"ActiveLoc" in resp.data
        assert b"OldLoc" not in resp.data

        resp = client.get("/locations", query_string={"archived": "archived"})
        assert b"ActiveLoc" not in resp.data
        assert b"OldLoc" in resp.data

        resp = client.get("/locations", query_string={"archived": "all"})
        assert b"ActiveLoc" in resp.data and b"OldLoc" in resp.data

        resp = client.get(
            "/locations",
            query_string={"name_query": "Old", "match_mode": "contains", "archived": "all"},
        )
        assert b"OldLoc" in resp.data
        assert b"ActiveLoc" not in resp.data


def test_location_items_manage_gl_overrides(client, app):
    email, *_ = setup_data(app)
    with app.app_context():
        gl_default = (
            GLCode.query.filter(GLCode.code.like("5%"))
            .order_by(GLCode.id)
            .first()
        )
        if gl_default is None:
            gl_default = GLCode(code="5002")
            db.session.add(gl_default)
            db.session.flush()
        gl_override = GLCode.query.filter_by(code="5001").first()
        if gl_override is None:
            gl_override = GLCode(code="5001")
            db.session.add(gl_override)
            db.session.flush()
        item_one = Item.query.filter_by(name="Flour").first()
        item_one.purchase_gl_code_id = gl_default.id
        item_two = Item(
            name="Sugar",
            base_unit="gram",
            purchase_gl_code_id=gl_default.id,
        )
        location = Location(name="Bakery")
        db.session.add_all([item_two, location])
        db.session.flush()
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=location.id,
                    item_id=item_one.id,
                    expected_count=3,
                    purchase_gl_code_id=gl_override.id,
                ),
                LocationStandItem(
                    location_id=location.id,
                    item_id=item_two.id,
                    expected_count=7,
                ),
            ]
        )
        db.session.commit()
        location_id = location.id
        first_item_id = item_one.id
        second_item_id = item_two.id
        override_id = gl_override.id
        default_id = gl_default.id
    with client:
        login(client, email, "pass")
        with captured_templates(app) as templates:
            resp = client.get(f"/locations/{location_id}/items")
            assert resp.status_code == 200
            template, context = templates[0]
            assert template.name == "locations/location_items.html"
            assert context["location"].id == location_id
            assert context["entries"].total == 2
            assert any(gl.id == default_id for gl in context["purchase_gl_codes"])
            assert "per_page" in context["pagination_args"]
            assert context["pagination_args"]["per_page"] == str(context["per_page"])
        resp = client.post(
            f"/locations/{location_id}/items?page=1",
            data={
                f"location_gl_code_{first_item_id}": "",
                f"location_gl_code_{second_item_id}": str(override_id),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Item GL codes updated successfully" in resp.data
    with app.app_context():
        first = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=first_item_id
        ).first()
        second = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=second_item_id
        ).first()
        assert first.purchase_gl_code_id is None
        assert second.purchase_gl_code_id == override_id


def test_location_items_add_and_remove_item(client, app):
    email, *_ = setup_data(app)
    with app.app_context():
        location = Location(name="Warehouse")
        extra_item = Item(name="Napkins", base_unit="each")
        db.session.add_all([location, extra_item])
        db.session.commit()
        location_id = location.id
        extra_item_id = extra_item.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/locations/{location_id}/items/add",
            data={"item_id": extra_item_id, "expected_count": "4"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Item added to location" in resp.data

    with app.app_context():
        record = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=extra_item_id
        ).first()
        assert record is not None
        assert record.expected_count == 4

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/locations/{location_id}/items/{extra_item_id}/delete",
            data={"submit": "Delete"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Item removed from location" in resp.data

    with app.app_context():
        assert (
            LocationStandItem.query.filter_by(
                location_id=location_id, item_id=extra_item_id
            ).first()
            is None
        )


def test_location_items_cannot_remove_recipe_item(client, app):
    email, prod_id, menu_id = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/locations/add",
            data={"name": "Kitchen", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        location = Location.query.filter_by(name="Kitchen").first()
        assert location is not None
        location_id = location.id
        protected_item_id = location.stand_items[0].item_id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/locations/{location_id}/items/{protected_item_id}/delete",
            data={"submit": "Delete"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"cannot be removed" in resp.data

    with app.app_context():
        assert LocationStandItem.query.filter_by(
            location_id=location_id, item_id=protected_item_id
        ).count() == 1


def test_item_locations_hide_editable_gl_controls_for_view_only_users(client, app):
    email, _, _ = setup_data(app)
    with app.app_context():
        item = Item.query.filter_by(name="Flour").first()
        assert item is not None
        location = Location(name="Read Only Location")
        db.session.add(location)
        db.session.flush()
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=8,
            )
        )
        viewer = User(
            email="item-locations-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(viewer)
        db.session.commit()
        grant_permissions(
            viewer,
            "items.view",
            group_name="Item Locations View Only",
            description="Can review item locations without editing them.",
        )
        item_id = item.id

    with client:
        login(client, "item-locations-viewer@example.com", "pass")
        response = client.get(f"/items/{item_id}/locations", follow_redirects=True)

    assert response.status_code == 200
    assert b"Save Changes" not in response.data
    assert b'name="location_gl_code_' not in response.data
    assert b"Use Item Default" in response.data


def test_location_items_hide_add_remove_and_gl_override_controls_for_view_only_users(
    client, app
):
    email, _, _ = setup_data(app)
    with app.app_context():
        location = Location(name="Read Only Warehouse")
        extra_item = Item(name="View Only Napkins", base_unit="each")
        db.session.add_all([location, extra_item])
        db.session.flush()
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=extra_item.id,
                expected_count=5,
            )
        )
        viewer = User(
            email="location-items-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(viewer)
        db.session.commit()
        grant_permissions(
            viewer,
            "locations.view",
            group_name="Location Items View Only",
            description="Can review location items without editing them.",
        )
        location_id = location.id

    with client:
        login(client, "location-items-viewer@example.com", "pass")
        response = client.get(
            f"/locations/{location_id}/items",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Save Changes" not in response.data
    assert b"/items/add" not in response.data
    assert b'name="location_gl_code_' not in response.data
    assert b"Remove" not in response.data
    assert b"View only" in response.data


def test_copy_stand_sheet_overwrites_and_supports_multiple_targets(client, app):
    email, prod_id, menu_id = setup_data(app)
    with app.app_context():
        # second product to show overwrite behaviour
        prod2 = Product(name="Pie", price=4.0, cost=2.0)
        db.session.add(prod2)
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=prod2.id,
                item_id=Item.query.first().id,
                unit_id=ItemUnit.query.first().id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        prod2_id = prod2.id
        menu_target = Menu(name="Target Menu", description="Second product")
        menu_target.products.append(prod2)
        db.session.add(menu_target)
        db.session.commit()
        target_menu_id = menu_target.id
    with client:
        login(client, email, "pass")
        # Source location with product 1
        client.post(
            "/locations/add",
            data={"name": "Source", "menu_id": str(menu_id)},
            follow_redirects=True,
        )
        # Targets initially with product 2
        client.post(
            "/locations/add",
            data={"name": "Target1", "menu_id": str(target_menu_id)},
            follow_redirects=True,
        )
        client.post(
            "/locations/add",
            data={"name": "Target2", "menu_id": str(target_menu_id)},
            follow_redirects=True,
        )

    with app.app_context():
        source = Location.query.filter_by(name="Source").first()
        t1 = Location.query.filter_by(name="Target1").first()
        t2 = Location.query.filter_by(name="Target2").first()
        # set expected count on source stand item
        src_item = LocationStandItem.query.filter_by(location_id=source.id).first()
        src_item.expected_count = 5
        db.session.commit()
        src_item_id = src_item.item_id
        source_id = source.id
        t1_id = t1.id
        t2_id = t2.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/locations/{source_id}/copy_items",
            json={"target_ids": [t1_id, t2_id]},
        )
        assert resp.status_code == 200
        assert resp.json["success"]

    with app.app_context():
        for loc_id in (t1_id, t2_id):
            loc = db.session.get(Location, loc_id)
            # menu and products overwritten to match source exactly
            assert loc.current_menu_id == menu_id
            assert [p.id for p in loc.products] == [prod_id]
            stand_items = LocationStandItem.query.filter_by(location_id=loc.id).all()
            assert len(stand_items) == 1
            assert stand_items[0].item_id == src_item_id
            assert stand_items[0].expected_count == 5
