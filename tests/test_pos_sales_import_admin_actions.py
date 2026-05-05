import os
import re
from datetime import date as date_cls

import pytest

from app import db
from app.models import (
    Event,
    EventLocation,
    Location,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Product,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
    User,
)
from tests.permission_helpers import grant_permissions
from tests.utils import extract_csrf_token, login
from werkzeug.security import generate_password_hash


def _seed_import_with_unresolved_rows(app, *, message_id: str = "msg-map-1"):
    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id=message_id,
            attachment_filename="sales.xls",
            attachment_sha256=(message_id[-1] or "e") * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="North Stand Legacy",
            normalized_location_name="north_stand_legacy",
            location_id=None,
            parse_index=0,
        )
        db.session.add(location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=location.id,
            source_product_name="Mega Pretzel",
            normalized_product_name="mega_pretzel",
            product_id=None,
            quantity=2.0,
            computed_unit_price=7.5,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        return sales_import.id, location.id, row.id


def _seed_import_with_existing_location_mapping(
    app, *, message_id: str = "msg-mapped-location"
):
    with app.app_context():
        mapped_location = Location(name=f"Sugar Rush {message_id}")
        override_location = Location(name=f"Caesar Bar {message_id}")
        product = Product(name=f"Mapped Product {message_id}", price=6.0, cost=2.5)
        db.session.add_all([mapped_location, override_location, product])
        db.session.flush()

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id=message_id,
            attachment_filename="sales.xls",
            attachment_sha256=(message_id[-1] or "m") * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=mapped_location.name,
            normalized_location_name=mapped_location.name.lower().replace(" ", "_"),
            location_id=mapped_location.id,
            parse_index=0,
        )
        db.session.add(location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=location.id,
                source_product_name=product.name,
                normalized_product_name=product.name.lower().replace(" ", "_"),
                product_id=product.id,
                quantity=1.0,
                computed_unit_price=product.price,
                parse_index=0,
            )
        )
        db.session.commit()

        return sales_import.id, location.id, mapped_location.id, override_location.id


def test_mapping_resolution_create_or_map_flow_updates_rows_and_aliases(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    import_id, location_import_id, row_id = _seed_import_with_unresolved_rows(app)

    with app.app_context():
        mapped_location = Location(name="North Stand")
        mapped_product = Product(name="Pretzel", price=5.0, cost=2.0)
        db.session.add_all([mapped_location, mapped_product])
        db.session.commit()
        mapped_location_id = mapped_location.id
        mapped_product_id = mapped_product.id

    with client:
        login(client, admin_email, admin_pass)

        detail_response = client.get(
            f"/controlpanel/sales-imports/{import_id}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        detail_html = detail_response.get_data(as_text=True)
        assert 'data-role="product-search-input"' in detail_html
        assert 'data-role="product-search-results"' in detail_html
        assert 'list="terminal-product-options"' not in detail_html
        assert "terminal_sales_mapping.js" in detail_html
        assert f'Pretzel (ID: {mapped_product_id})' in detail_html

        map_location_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "map_location",
                "location_import_id": location_import_id,
                "target_location_id": mapped_location_id,
            },
            follow_redirects=True,
        )
        assert map_location_response.status_code == 200
        assert b"Location mapping saved" in map_location_response.data

        map_product_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "map_product",
                "row_id": row_id,
                "target_product_id": mapped_product_id,
            },
            follow_redirects=True,
        )
        assert map_product_response.status_code == 200
        assert b"Product mapping saved" in map_product_response.data

        create_location_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "create_location",
                "location_import_id": location_import_id,
                "new_location_name": "Created Stand",
            },
            follow_redirects=True,
        )
        assert create_location_response.status_code == 200
        assert b"Location created and mapping saved" in create_location_response.data

        create_product_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "create_product",
                "row_id": row_id,
            },
            follow_redirects=True,
        )
        assert create_product_response.status_code == 200
        assert b"Create Product for Sales Import" in create_product_response.data
        assert b"Mega Pretzel" in create_product_response.data
        create_product_html = create_product_response.get_data(as_text=True)
        assert re.search(
            r'name="price"[^>]*value="7\\.50"',
            create_product_html,
        )

        save_created_product_response = client.post(
            f"/products/create?sales_import_id={import_id}&import_row_id={row_id}&return_location_id={location_import_id}",
            data={
                "name": "Created Pretzel",
                "price": 6.5,
                "cost": 2.5,
            },
            follow_redirects=True,
        )
        assert save_created_product_response.status_code == 200
        assert b"Product created and mapped back to the sales import" in save_created_product_response.data

    with app.app_context():
        import_record = db.session.get(PosSalesImport, import_id)
        location_record = db.session.get(PosSalesImportLocation, location_import_id)
        row_record = db.session.get(PosSalesImportRow, row_id)

        created_location = Location.query.filter_by(name="Created Stand").one()
        created_product = Product.query.filter_by(name="Created Pretzel").one()
        location_alias = TerminalSaleLocationAlias.query.filter_by(
            normalized_name="north_stand_legacy"
        ).one()
        product_alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name="mega_pretzel"
        ).one()

        assert import_record is not None
        assert location_record.location_id == created_location.id
        assert row_record.product_id == created_product.id
        assert created_product.price == pytest.approx(6.5)
        assert location_alias.location_id == created_location.id
        assert product_alias.product_id == created_product.id


def test_sales_import_detail_shows_override_location_mapping_for_existing_match(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    import_id, _, mapped_location_id, override_location_id = (
        _seed_import_with_existing_location_mapping(
            app, message_id="msg-existing-location-map-ui"
        )
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            f"/controlpanel/sales-imports/{import_id}",
            follow_redirects=True,
        )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Override location mapping" in body
    assert "Currently mapped to Sugar Rush msg-existing-location-map-ui" in body
    assert f'option value="{mapped_location_id}" selected' in body
    assert f'value="{override_location_id}"' in body


@pytest.mark.parametrize(
    "action,data",
    [
        ("save_sales_date", {"sales_date": "2026-04-15"}),
        ("map_location", {"location_import_id": 1, "target_location_id": 1}),
        ("create_location", {"location_import_id": 1, "new_location_name": "Created"}),
        ("map_product", {"row_id": 1, "target_product_id": 1}),
        ("create_product", {"row_id": 1, "new_product_name": "Created Product"}),
        ("resolve_row_price", {"row_id": 1, "price_resolution": "skip"}),
        ("refresh_auto_mapping", {}),
        ("approve_import", {}),
        (
            "undo_approved_import",
            {"reversal_reason": "rollback", "confirm_reversal": "1"},
        ),
        ("delete_import", {"deletion_reason": "cleanup"}),
    ],
)
def test_sales_import_actions_require_admin_authorization(client, app, action, data):
    import_id, _, _ = _seed_import_with_unresolved_rows(app, message_id=f"msg-{action}")

    with app.app_context():
        user = User(email=f"{action}@example.com", password="", active=True, is_admin=False)
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    response = client.post(
        f"/controlpanel/sales-imports/{import_id}",
        data={"action": action, **data},
        follow_redirects=False,
    )
    assert response.status_code == 403

    list_response = client.post(
        "/controlpanel/sales-imports",
        data={"action": "approve_import", "import_id": import_id},
        follow_redirects=False,
    )
    assert list_response.status_code == 403


def test_sales_import_and_terminal_mapping_actions_require_csrf(client, app):
    app.config.update({"WTF_CSRF_ENABLED": True})
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    import_id, location_import_id, row_id = _seed_import_with_unresolved_rows(
        app, message_id="msg-csrf"
    )

    with app.app_context():
        location = Location(name="CSRF Stand")
        product = Product(name="CSRF Product", price=1.0, cost=0.5)
        location_alias = TerminalSaleLocationAlias(
            source_name="Legacy CSRF Stand",
            normalized_name="legacy_csrf_stand",
            location=location,
        )
        db.session.add_all([location, product])
        db.session.flush()
        db.session.add(location_alias)
        db.session.commit()
        location_id = location.id
        location_alias_id = location_alias.id
        product_id = product.id

    with client:
        login(client, admin_email, admin_pass)

        # Location mapping removal should be CSRF-protected.
        mappings_without_csrf = client.post(
            f"/locations/{location_id}/terminal_sale_aliases/{location_alias_id}/delete",
            follow_redirects=False,
        )
        assert mappings_without_csrf.status_code == 400

        detail_page = client.get(f"/controlpanel/sales-imports/{import_id}")
        csrf_token = extract_csrf_token(detail_page)

        action_payloads = [
            {"action": "save_sales_date", "sales_date": "2026-04-15"},
            {"action": "map_location", "location_import_id": location_import_id, "target_location_id": location_id},
            {"action": "create_location", "location_import_id": location_import_id, "new_location_name": "CSRF Created Stand"},
            {"action": "map_product", "row_id": row_id, "target_product_id": product_id},
            {"action": "create_product", "row_id": row_id, "new_product_name": "CSRF Created Product"},
            {"action": "resolve_row_price", "row_id": row_id, "price_resolution": "skip"},
            {"action": "refresh_auto_mapping"},
            {"action": "approve_import"},
            {"action": "undo_approved_import", "reversal_reason": "rollback", "confirm_reversal": "1"},
            {"action": "delete_import", "deletion_reason": "cleanup"},
        ]

        for payload in action_payloads:
            denied = client.post(
                f"/controlpanel/sales-imports/{import_id}",
                data=payload,
                follow_redirects=False,
            )
            assert denied.status_code == 400

            allowed = client.post(
                f"/controlpanel/sales-imports/{import_id}",
                data={**payload, "csrf_token": csrf_token},
                follow_redirects=False,
            )
            assert allowed.status_code in {302, 303}

        denied_list = client.post(
            "/controlpanel/sales-imports",
            data={"action": "approve_import", "import_id": import_id},
            follow_redirects=False,
        )
        assert denied_list.status_code == 400

        allowed_list = client.post(
            "/controlpanel/sales-imports",
            data={
                "action": "approve_import",
                "import_id": import_id,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert allowed_list.status_code in {302, 303}


def test_saving_sales_date_auto_assigns_matching_event_location(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    import_id, location_import_id, _ = _seed_import_with_unresolved_rows(
        app, message_id="msg-save-sales-date"
    )

    with app.app_context():
        import_record = db.session.get(PosSalesImport, import_id)
        import_record.sales_date = None
        mapped_location = Location(name="North Stand")
        event = Event(
            name="North Stand Event",
            start_date=date_cls(2026, 4, 15),
            end_date=date_cls(2026, 4, 15),
        )
        db.session.add_all([mapped_location, event])
        db.session.flush()
        event_location = EventLocation(
            event_id=event.id,
            location_id=mapped_location.id,
        )
        db.session.add(event_location)
        db.session.flush()
        mapped_location_id = mapped_location.id
        event_location_id = event_location.id
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)

        map_location_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "map_location",
                "location_import_id": location_import_id,
                "target_location_id": mapped_location_id,
            },
            follow_redirects=True,
        )
        assert map_location_response.status_code == 200
        assert b"Location mapping saved" in map_location_response.data

        save_date_response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "save_sales_date",
                "sales_date": "2026-04-15",
                "selected_location_id": location_import_id,
            },
            follow_redirects=True,
        )
        assert save_date_response.status_code == 200
        assert b"Sales date saved" in save_date_response.data
        assert b"This import location is linked to an event location" in save_date_response.data
        assert b"North Stand Event" in save_date_response.data

    with app.app_context():
        import_record = db.session.get(PosSalesImport, import_id)
        location_record = db.session.get(PosSalesImportLocation, location_import_id)

        assert import_record.sales_date.isoformat() == "2026-04-15"
        assert location_record.event_location_id == event_location_id


def test_sales_import_detail_hides_create_location_without_locations_create_permission(
    client, app
):
    import_id, _, _ = _seed_import_with_unresolved_rows(
        app, message_id="msg-create-location-ui"
    )

    with app.app_context():
        user = User(
            email="sales-import-reviewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.manage",
            group_name="Sales Import Reviewers",
            description="Can manage sales imports without creating locations.",
        )

    with client:
        login(client, "sales-import-reviewer@example.com", "pass")
        response = client.get(
            f"/controlpanel/sales-imports/{import_id}",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Resolve location mapping" in response.data
    assert b"Save Mapping" in response.data
    assert b"Or create new location" not in response.data
    assert b"Create + Map" not in response.data


def test_sales_import_create_location_requires_locations_create_permission(
    client, app
):
    import_id, location_import_id, _ = _seed_import_with_unresolved_rows(
        app, message_id="msg-create-location-permission"
    )

    with app.app_context():
        user = User(
            email="sales-import-blocked@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.manage",
            group_name="Sales Import Managers Without Location Create",
            description="Can review imports but cannot create locations.",
        )

    with client:
        login(client, "sales-import-blocked@example.com", "pass")
        detail_page = client.get(f"/controlpanel/sales-imports/{import_id}")
        csrf_token = extract_csrf_token(detail_page, required=False)
        response = client.post(
            f"/controlpanel/sales-imports/{import_id}",
            data={
                "action": "create_location",
                "location_import_id": location_import_id,
                "new_location_name": "Blocked Created Stand",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

    assert response.status_code == 403

    with app.app_context():
        assert Location.query.filter_by(name="Blocked Created Stand").first() is None
